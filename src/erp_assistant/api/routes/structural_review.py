from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from erp_assistant.api.dependencies import get_semantic_review_session
from erp_assistant.api.routes.semantic_review import AdminSemanticApiError
from erp_assistant.api.schemas.structural_review import (
    StructuralCorrectionRequest,
    StructuralReviewItemDetail,
    StructuralReviewListResponse,
    StructuralReviewRequest,
    StructuralReviewResultResponse,
)
from erp_assistant.api.structural_review_serializers import item_detail, item_summary
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus, ReviewSource
from erp_assistant.persistence.postgres.models import KnowledgeItem, KnowledgeVersionRecord
from erp_assistant.structural.services.knowledge_review_service import KnowledgeReviewService
from erp_assistant.structural.canonical.enums import ReviewStatus

router = APIRouter(
    prefix="/structural-review/items",
    tags=["local structural review (provisional)"],
)
SessionDependency = Annotated[Session, Depends(get_semantic_review_session)]
REVIEWABLE_VERSION_STATUSES = {
    KnowledgeVersionStatus.IMPORTED,
    KnowledgeVersionStatus.ACTIVE,
}


def _version(session: Session, version_id: uuid.UUID) -> KnowledgeVersionRecord:
    version = session.get(KnowledgeVersionRecord, version_id)
    if version is None:
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        )
    return version


def _item(session: Session, item_id: uuid.UUID) -> KnowledgeItem:
    item = session.get(KnowledgeItem, item_id)
    if item is None:
        raise AdminSemanticApiError(
            404,
            "KnowledgeItemNotFoundError",
            "not_found",
            "Elemento estructural no encontrado.",
        )
    return item


def _ensure_reviewable_version(item: KnowledgeItem) -> None:
    if item.knowledge_version.status not in REVIEWABLE_VERSION_STATUSES:
        raise AdminSemanticApiError(
            409,
            "KnowledgeVersionNotReviewableError",
            "inactive_version",
            "La versión solicitada no admite nuevas revisiones.",
            current_status=item.current_review_status,
        )


def _ensure_expected(item: KnowledgeItem, body: StructuralReviewRequest) -> None:
    if item.current_review_status != body.expected_status or item.review_revision != body.expected_revision:
        raise AdminSemanticApiError(
            409,
            "StructuralRevisionConflictError",
            "review_conflict",
            "El elemento cambió; recargue el detalle antes de revisar.",
            current_status=item.current_review_status,
        )


def _result(action: str, item: KnowledgeItem, session: Session) -> StructuralReviewResultResponse:
    return StructuralReviewResultResponse(
        **item_detail(item, session).model_dump(),
        performed_action=action,
    )


@router.get("", response_model=StructuralReviewListResponse)
def list_items(
    session: SessionDependency,
    knowledge_version_id: uuid.UUID,
    status: ReviewStatus | None = None,
    entity_type: str | None = Query(default=None, max_length=60),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> StructuralReviewListResponse:
    _version(session, knowledge_version_id)
    conditions = [KnowledgeItem.knowledge_version_id == knowledge_version_id]
    if status is not None:
        conditions.append(KnowledgeItem.current_review_status == status)
    if entity_type:
        conditions.append(KnowledgeItem.entity_type == entity_type)
    if search:
        needle = f"%{search.strip()}%"
        conditions.append(
            or_(
                KnowledgeItem.title.ilike(needle),
                KnowledgeItem.canonical_id.ilike(needle),
                KnowledgeItem.route.ilike(needle),
            )
        )

    total = int(
        session.scalar(select(func.count()).select_from(KnowledgeItem).where(*conditions)) or 0
    )
    items = list(
        session.scalars(
            select(KnowledgeItem)
            .where(*conditions)
            .order_by(KnowledgeItem.entity_type, KnowledgeItem.title, KnowledgeItem.canonical_id)
            .offset(offset)
            .limit(limit)
        )
    )
    status_rows = session.execute(
        select(KnowledgeItem.current_review_status, func.count())
        .where(KnowledgeItem.knowledge_version_id == knowledge_version_id)
        .group_by(KnowledgeItem.current_review_status)
    ).all()
    return StructuralReviewListResponse(
        items=tuple(item_summary(item) for item in items),
        status_counts={str(status_value): int(count) for status_value, count in status_rows},
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(items) if offset + len(items) < total else None,
    )


@router.get("/{item_id}", response_model=StructuralReviewItemDetail)
def get_item(item_id: uuid.UUID, session: SessionDependency) -> StructuralReviewItemDetail:
    return item_detail(_item(session, item_id), session)


def _review(
    item_id: uuid.UUID,
    body: StructuralReviewRequest,
    session: Session,
    action: Literal["approve", "correct", "reject", "reset_to_pending"],
) -> StructuralReviewResultResponse:
    item = _item(session, item_id)
    _ensure_reviewable_version(item)
    _ensure_expected(item, body)
    service = KnowledgeReviewService(session)
    try:
        kwargs = {
            "reviewer": body.reviewer_id,
            "notes": body.reason,
            "expected_revision": body.expected_revision,
            "source": ReviewSource.API,
        }
        if action == "correct":
            assert isinstance(body, StructuralCorrectionRequest)
            changed = service.correct(item.id, body.corrected_payload, **kwargs)
        elif action == "reset_to_pending":
            changed = service.reset_to_pending(item.id, **kwargs)
        else:
            changed = getattr(service, action)(item.id, **kwargs)
        return _result(action, changed, session)
    except LookupError as exc:
        raise AdminSemanticApiError(
            404,
            type(exc).__name__,
            "not_found",
            "Elemento estructural no encontrado.",
        ) from exc
    except ValueError as exc:
        message = str(exc)
        conflict = "Transición no permitida" in message or "concurrente" in message
        raise AdminSemanticApiError(
            409 if conflict else 422,
            type(exc).__name__,
            "review_conflict" if conflict else "invalid_review_input",
            "La revisión cambió; recargue el elemento antes de continuar."
            if conflict
            else "La revisión estructural no cumple el contrato permitido.",
            current_status=item.current_review_status,
        ) from exc


@router.post("/{item_id}/approve", response_model=StructuralReviewResultResponse)
def approve(item_id: uuid.UUID, body: StructuralReviewRequest, session: SessionDependency):
    return _review(item_id, body, session, "approve")


@router.post("/{item_id}/correct", response_model=StructuralReviewResultResponse)
def correct(item_id: uuid.UUID, body: StructuralCorrectionRequest, session: SessionDependency):
    return _review(item_id, body, session, "correct")


@router.post("/{item_id}/reject", response_model=StructuralReviewResultResponse)
def reject(item_id: uuid.UUID, body: StructuralReviewRequest, session: SessionDependency):
    if not body.reason:
        raise AdminSemanticApiError(
            422,
            "ReviewReasonRequiredError",
            "invalid_review_input",
            "El rechazo requiere una justificación.",
        )
    return _review(item_id, body, session, "reject")


@router.post("/{item_id}/reset", response_model=StructuralReviewResultResponse)
def reset(item_id: uuid.UUID, body: StructuralReviewRequest, session: SessionDependency):
    return _review(item_id, body, session, "reset_to_pending")
