from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.dependencies import get_admin_read_session, get_semantic_review_session
from src.api.routes.semantic_review import AdminSemanticApiError
from src.api.schemas.structural_publication_review import (
    StructuralPublicationApprovalResponse,
    StructuralPublicationApproveRequest,
    StructuralPublicationReviewItemResponse,
    StructuralPublicationReviewPackageResponse,
    StructuralPublicationReviewSummaryResponse,
)
from src.database.services.structural_publication_review_service import (
    StructuralPublicationReviewConflictError,
    StructuralPublicationReviewError,
    StructuralPublicationReviewService,
)

router = APIRouter(
    prefix="/knowledge-versions",
    tags=["structural publication review"],
)
ReadSession = Annotated[Session, Depends(get_admin_read_session)]
WriteSession = Annotated[Session, Depends(get_semantic_review_session)]
ScopeType = Literal["screen", "module", "system", "unscoped"]


def _item(value):
    return StructuralPublicationReviewItemResponse(**value.__dict__)


def _package(value):
    return StructuralPublicationReviewPackageResponse(
        **{
            **value.__dict__,
            "review_items": tuple(_item(item) for item in value.review_items),
        }
    )


def _raise_service_error(exc: Exception):
    if isinstance(exc, LookupError):
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        ) from exc
    if isinstance(exc, StructuralPublicationReviewConflictError):
        raise AdminSemanticApiError(
            409,
            type(exc).__name__,
            "publication_review_conflict",
            str(exc),
        ) from exc
    raise AdminSemanticApiError(
        409,
        type(exc).__name__,
        "invalid_publication_review",
        str(exc),
    ) from exc


@router.get(
    "/{knowledge_version_id}/publication-review-packages",
    response_model=StructuralPublicationReviewSummaryResponse,
)
def publication_review_packages(
    knowledge_version_id: uuid.UUID,
    session: ReadSession,
    pending_only: bool = False,
    scope_type: ScopeType | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        result = StructuralPublicationReviewService(session).build(
            knowledge_version_id,
            pending_only=pending_only,
            scope_type=scope_type,
            limit=limit,
            offset=offset,
        )
    except (LookupError, StructuralPublicationReviewError) as exc:
        _raise_service_error(exc)
    return StructuralPublicationReviewSummaryResponse(
        **{
            **result.__dict__,
            "packages": tuple(_package(package) for package in result.packages),
        }
    )


@router.post(
    "/{knowledge_version_id}/publication-review-packages/approve-pending",
    response_model=StructuralPublicationApprovalResponse,
)
def approve_publication_package(
    knowledge_version_id: uuid.UUID,
    body: StructuralPublicationApproveRequest,
    session: WriteSession,
):
    try:
        result = StructuralPublicationReviewService(session).approve_pending(
            knowledge_version_id,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            expected_package_hash=body.expected_package_hash,
            reviewer=body.reviewer_id,
            reason=body.reason,
        )
    except (LookupError, StructuralPublicationReviewError) as exc:
        _raise_service_error(exc)
    return StructuralPublicationApprovalResponse(
        approved_count=result.approved_count,
        package=_package(result.package),
    )
