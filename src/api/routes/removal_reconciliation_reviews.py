from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.dependencies import get_admin_read_session, get_semantic_review_session
from src.api.routes.semantic_review import AdminSemanticApiError
from src.api.schemas.removal_reconciliation_review import (
    RemovalReviewActionResponse,
    RemovalReviewDecisionResponse,
    RemovalReviewHistoryResponse,
    RemovalReviewRequest,
    RemovalReviewResultResponse,
    RemovalReviewSetResponse,
)
from src.database.enums import ReviewSource
from src.database.services import (
    RemovalReconciliationReviewError,
    RemovalReconciliationReviewNotPreparedError,
    RemovalReconciliationReviewService,
)

router = APIRouter(
    prefix="/removal-reconciliation-reviews",
    tags=["removal reconciliation reviews"],
)
ReadSession = Annotated[Session, Depends(get_admin_read_session)]
WriteSession = Annotated[Session, Depends(get_semantic_review_session)]


def _decision_response(value) -> RemovalReviewDecisionResponse:
    return RemovalReviewDecisionResponse(**value.__dict__)


def _set_response(value) -> RemovalReviewSetResponse:
    return RemovalReviewSetResponse(
        **{
            **value.__dict__,
            "decisions": tuple(_decision_response(item) for item in value.decisions),
        }
    )


def _review_error(exc: Exception) -> AdminSemanticApiError:
    if isinstance(exc, RemovalReconciliationReviewNotPreparedError):
        return AdminSemanticApiError(
            409,
            type(exc).__name__,
            "removal_review_not_prepared",
            str(exc),
        )
    if isinstance(exc, LookupError):
        return AdminSemanticApiError(
            404,
            type(exc).__name__,
            "not_found",
            str(exc),
        )
    message = str(exc)
    conflict = "Conflicto" in message or "Transición no permitida" in message
    return AdminSemanticApiError(
        409 if conflict else 422,
        type(exc).__name__,
        "review_conflict" if conflict else "invalid_removal_review",
        message,
    )


@router.post("/{candidate_version_id}/prepare", response_model=RemovalReviewSetResponse)
def prepare_review(candidate_version_id: uuid.UUID, session: WriteSession):
    try:
        service = RemovalReconciliationReviewService(session)
        return _set_response(service.prepare(candidate_version_id))
    except (RemovalReconciliationReviewError, LookupError) as exc:
        raise _review_error(exc) from exc


@router.get("/{candidate_version_id}", response_model=RemovalReviewSetResponse)
def get_review(candidate_version_id: uuid.UUID, session: ReadSession):
    try:
        return _set_response(RemovalReconciliationReviewService(session).get(candidate_version_id))
    except (RemovalReconciliationReviewError, LookupError) as exc:
        raise _review_error(exc) from exc


@router.get(
    "/decisions/{decision_id}/history",
    response_model=RemovalReviewHistoryResponse,
)
def review_history(decision_id: uuid.UUID, session: ReadSession):
    try:
        actions = RemovalReconciliationReviewService(session).history(decision_id)
    except (RemovalReconciliationReviewError, LookupError) as exc:
        raise _review_error(exc) from exc
    return RemovalReviewHistoryResponse(
        decision_id=str(decision_id),
        actions=tuple(
            RemovalReviewActionResponse(
                id=str(action.id),
                action=action.action.value,
                previous_decision=(
                    action.previous_decision.value if action.previous_decision else None
                ),
                new_decision=action.new_decision.value if action.new_decision else None,
                review_notes=action.review_notes,
                reviewer_subject=action.reviewer_subject,
                source=action.source.value,
                decision_fingerprint=action.decision_fingerprint,
                created_at=action.created_at,
            )
            for action in actions
        ),
    )


def _change(
    decision_id: uuid.UUID,
    body: RemovalReviewRequest,
    session: Session,
    action: Literal["confirm_retain", "confirm_remove", "reset_to_pending"],
) -> RemovalReviewResultResponse:
    service = RemovalReconciliationReviewService(session)
    try:
        method = getattr(service, action)
        value = method(
            decision_id,
            reviewer=body.reviewer_id,
            reason=body.reason,
            expected_revision=body.expected_revision,
            source=ReviewSource.API,
        )
    except (RemovalReconciliationReviewError, LookupError) as exc:
        raise _review_error(exc) from exc
    return RemovalReviewResultResponse(
        **value.__dict__,
        performed_action=action,
    )


@router.post(
    "/decisions/{decision_id}/confirm-retain",
    response_model=RemovalReviewResultResponse,
)
def confirm_retain(decision_id: uuid.UUID, body: RemovalReviewRequest, session: WriteSession):
    return _change(decision_id, body, session, "confirm_retain")


@router.post(
    "/decisions/{decision_id}/confirm-remove",
    response_model=RemovalReviewResultResponse,
)
def confirm_remove(decision_id: uuid.UUID, body: RemovalReviewRequest, session: WriteSession):
    return _change(decision_id, body, session, "confirm_remove")


@router.post(
    "/decisions/{decision_id}/reset",
    response_model=RemovalReviewResultResponse,
)
def reset(decision_id: uuid.UUID, body: RemovalReviewRequest, session: WriteSession):
    return _change(decision_id, body, session, "reset_to_pending")
