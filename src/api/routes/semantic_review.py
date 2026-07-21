from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from src.analysis.generation.errors import ScreenPurposeGenerationError
from src.analysis.validators.screen_purpose_grounding import validate_capability_grounding
from src.analysis.validators.screen_purpose_validator import allowed_references
from src.api.dependencies import get_semantic_review_session
from src.api.schemas.semantic_review import (
    CorrectionRequest,
    EffectivePayloadResponse,
    ReviewRequest,
    ReviewResultResponse,
    SemanticApiErrorResponse,
    SemanticProposalDetailResponse,
    SemanticProposalListResponse,
)
from src.api.semantic_review_serializers import (
    proposal_detail,
    proposal_summary,
    validated_evidence_package,
)
from src.database.enums import SemanticType
from src.database.repositories import SemanticProposalRepository
from src.database.services import SemanticEffectivePayloadService, SemanticReviewService
from src.database.services.semantic_exceptions import (
    SemanticDomainError,
    SemanticHistoryIntegrityError,
    SemanticIdentityCollisionError,
    SemanticPayloadError,
    SemanticRevisionConflictError,
    SemanticSensitiveContentError,
    SemanticTransitionError,
)
from src.knowledge.canonical.enums import ReviewStatus

router = APIRouter(
    prefix="/semantic-proposals",
    tags=["local semantic review (provisional)"],
    responses={422: {"model": SemanticApiErrorResponse}},
)
SessionDependency = Annotated[Session, Depends(get_semantic_review_session)]


class AdminSemanticApiError(Exception):
    def __init__(
        self,
        status_code: int,
        error_class: str,
        category: str,
        detail: str,
        *,
        semantic_id: str | None = None,
        current_status: ReviewStatus | None = None,
    ):
        self.status_code = status_code
        self.payload = SemanticApiErrorResponse(
            error_class=error_class,
            category=category,
            semantic_id=semantic_id,
            current_status=current_status,
            detail=detail,
        )
        super().__init__(detail)


async def admin_semantic_error_handler(
    _request: Request, exc: AdminSemanticApiError
) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload.model_dump(mode="json"))


async def admin_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    if not request.url.path.startswith("/api/admin/semantic-proposals"):
        return await request_validation_exception_handler(request, exc)
    payload = SemanticApiErrorResponse(
        error_class="RequestValidationError",
        category="invalid_request",
        semantic_id=request.path_params.get("semantic_id"),
        detail="La solicitud no cumple el contrato administrativo.",
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


def _proposal(session: Session, semantic_id: str):
    proposal = SemanticProposalRepository(session).get_detail_by_semantic_id(semantic_id)
    if proposal is None:
        raise AdminSemanticApiError(
            404,
            "SemanticProposalNotFoundError",
            "not_found",
            "Propuesta semántica no encontrada.",
            semantic_id=semantic_id,
        )
    return proposal


@router.get(
    "",
    response_model=SemanticProposalListResponse,
    summary="Listar propuestas semánticas para revisión local",
)
def list_proposals(
    session: SessionDependency,
    status: ReviewStatus | None = None,
    semantic_type: SemanticType | None = None,
    erp_id: str | None = Query(default=None, max_length=160),
    knowledge_version_id: uuid.UUID | None = None,
    screen_id: str | None = Query(default=None, max_length=240),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SemanticProposalListResponse:
    rows, total = SemanticProposalRepository(session).list_admin_page(
        current_review_status=status,
        semantic_type=semantic_type,
        erp_id=erp_id,
        knowledge_version_id=knowledge_version_id,
        screen_id=screen_id,
        limit=limit,
        offset=offset,
    )
    return SemanticProposalListResponse(
        items=tuple(proposal_summary(proposal, count) for proposal, count in rows),
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(rows) if offset + len(rows) < total else None,
    )


@router.get("/{semantic_id}", response_model=SemanticProposalDetailResponse)
def get_proposal(
    semantic_id: str, session: SessionDependency
) -> SemanticProposalDetailResponse:
    try:
        return proposal_detail(_proposal(session, semantic_id), session)
    except (ValidationError, SemanticHistoryIntegrityError) as exc:
        raise AdminSemanticApiError(
            422,
            type(exc).__name__,
            "invalid_persisted_payload",
            "El contenido persistido no cumple el contrato semántico.",
            semantic_id=semantic_id,
        ) from exc


@router.get("/{semantic_id}/effective", response_model=EffectivePayloadResponse)
def get_effective(
    semantic_id: str, session: SessionDependency
) -> EffectivePayloadResponse:
    proposal = _proposal(session, semantic_id)
    service = SemanticEffectivePayloadService(session)
    try:
        effective = service.effective_payload(proposal.id)
        publishable = service.publishable_payload(proposal.id)
        from src.analysis.schemas import ScreenPurposeInference

        return EffectivePayloadResponse(
            semantic_id=semantic_id,
            current_review_status=proposal.current_review_status,
            review_revision=proposal.review_revision,
            effective_payload=ScreenPurposeInference.model_validate(effective),
            publishable_payload=(
                ScreenPurposeInference.model_validate(publishable)
                if publishable is not None
                else None
            ),
        )
    except (ValidationError, SemanticDomainError) as exc:
        raise AdminSemanticApiError(
            422,
            type(exc).__name__,
            "invalid_effective_payload",
            "No se pudo construir el payload efectivo de forma segura.",
            semantic_id=semantic_id,
        ) from exc


def _review(
    semantic_id: str,
    body: ReviewRequest,
    session: Session,
    action: Literal["approve", "correct", "reject"],
) -> ReviewResultResponse:
    proposal = _proposal(session, semantic_id)
    if proposal.current_review_status != body.expected_status:
        raise AdminSemanticApiError(
            409,
            "SemanticRevisionConflictError",
            "stale_status",
            "La propuesta cambió; recargue el detalle antes de revisar.",
            semantic_id=semantic_id,
            current_status=proposal.current_review_status,
        )
    service = SemanticReviewService(session)
    try:
        kwargs = {
            "expected_revision": body.expected_revision,
            "reviewer_subject": body.reviewer_id,
            "source": "admin_api",
            "review_notes": body.reason,
        }
        if action == "correct":
            assert isinstance(body, CorrectionRequest)
            if body.corrected_payload.semantic_type != str(proposal.semantic_type):
                raise SemanticPayloadError("semantic_type no puede cambiar")
            package = validated_evidence_package(proposal)
            if body.corrected_payload.screen_id != package.screen_id:
                raise SemanticPayloadError("screen_id no puede cambiar")
            unknown_refs = {
                reference
                for capability in body.corrected_payload.supported_capabilities
                for reference in capability.evidence_refs
                if reference not in allowed_references(package)
            }
            if unknown_refs:
                raise SemanticPayloadError("evidence_refs contiene referencias desconocidas")
            validate_capability_grounding(body.corrected_payload, package)
            changed = service.correct(
                proposal.id,
                body.corrected_payload.model_dump(mode="json"),
                **kwargs,
            )
        else:
            changed = getattr(service, action)(proposal.id, **kwargs)
        effective_service = SemanticEffectivePayloadService(session)
        effective = effective_service.effective_payload(changed.id)
        publishable = effective_service.publishable_payload(changed.id)
        from src.analysis.schemas import ScreenPurposeInference

        return ReviewResultResponse(
            action=action,
            semantic_id=semantic_id,
            current_review_status=changed.current_review_status,
            review_revision=changed.review_revision,
            effective_payload=ScreenPurposeInference.model_validate(effective),
            publishable_payload=(
                ScreenPurposeInference.model_validate(publishable)
                if publishable is not None
                else None
            ),
        )
    except (SemanticRevisionConflictError, SemanticTransitionError) as exc:
        current = SemanticProposalRepository(session).get_by_semantic_id(semantic_id)
        raise AdminSemanticApiError(
            409,
            type(exc).__name__,
            "review_conflict",
            "La propuesta cambió o la transición ya no está permitida; recargue el detalle.",
            semantic_id=semantic_id,
            current_status=current.current_review_status if current else None,
        ) from exc
    except (
        SemanticPayloadError,
        SemanticSensitiveContentError,
        SemanticHistoryIntegrityError,
        ScreenPurposeGenerationError,
        ValidationError,
    ) as exc:
        raise AdminSemanticApiError(
            422,
            type(exc).__name__,
            "invalid_review_input",
            "La revisión no cumple el contrato o no está respaldada por la evidencia.",
            semantic_id=semantic_id,
            current_status=proposal.current_review_status,
        ) from exc
    except SemanticIdentityCollisionError as exc:
        raise AdminSemanticApiError(
            409,
            type(exc).__name__,
            "identity_collision",
            "Se detectó una colisión de identidad semántica.",
            semantic_id=semantic_id,
        ) from exc
    except (OperationalError, DBAPIError) as exc:
        raise AdminSemanticApiError(
            503,
            type(exc).__name__,
            "storage_unavailable",
            "El almacenamiento semántico no está disponible.",
            semantic_id=semantic_id,
        ) from exc


@router.post("/{semantic_id}/approve", response_model=ReviewResultResponse)
def approve(semantic_id: str, body: ReviewRequest, session: SessionDependency):
    return _review(semantic_id, body, session, "approve")


@router.post("/{semantic_id}/correct", response_model=ReviewResultResponse)
def correct(semantic_id: str, body: CorrectionRequest, session: SessionDependency):
    return _review(semantic_id, body, session, "correct")


@router.post("/{semantic_id}/reject", response_model=ReviewResultResponse)
def reject(semantic_id: str, body: ReviewRequest, session: SessionDependency):
    return _review(semantic_id, body, session, "reject")
