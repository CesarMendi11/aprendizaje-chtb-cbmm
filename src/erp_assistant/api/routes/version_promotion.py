from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from erp_assistant.api.dependencies import get_admin_read_session, get_semantic_review_session
from erp_assistant.api.routes.semantic_review import AdminSemanticApiError
from erp_assistant.api.schemas.version_promotion import (
    KnowledgeVersionPromoteRequest,
    KnowledgeVersionPromotionResponse,
    PromotionAssessmentResponse,
    PromotionBlockerResponse,
)
from erp_assistant.structural.services.knowledge_promotion_service import (
    KnowledgePromotionBlockedError,
    KnowledgePromotionError,
    KnowledgePromotionService,
    PromotionAssessment,
)

router = APIRouter(
    prefix="/knowledge-versions",
    tags=["knowledge version promotion (provisional)"],
)
ReadSession = Annotated[Session, Depends(get_admin_read_session)]
WriteSession = Annotated[Session, Depends(get_semantic_review_session)]


def _assessment(value: PromotionAssessment) -> PromotionAssessmentResponse:
    return PromotionAssessmentResponse(
        knowledge_version_id=value.knowledge_version_id,
        knowledge_version=value.knowledge_version,
        erp_id=value.erp_id,
        version_status=value.version_status,
        promotable=value.promotable,
        bootstrap_promotion=value.bootstrap_promotion,
        promotion_mode=value.promotion_mode,
        current_active_version_id=value.current_active_version_id,
        current_active_knowledge_version=value.current_active_knowledge_version,
        required_entity_types=value.required_entity_types,
        required_review_counts=value.required_review_counts,
        all_review_counts=value.all_review_counts,
        replacement_review_counts=value.replacement_review_counts,
        diff_totals=value.diff_totals,
        pipeline_import_job_id=value.pipeline_import_job_id,
        source_canonical_job_id=value.source_canonical_job_id,
        source_reconciliation_job_id=value.source_reconciliation_job_id,
        removal_review_set_id=value.removal_review_set_id,
        decision_set_hash=value.decision_set_hash,
        build_warning_count=value.build_warning_count,
        blockers=tuple(
            PromotionBlockerResponse(
                code=blocker.code,
                message=blocker.message,
                count=blocker.count,
                entity_type=blocker.entity_type,
            )
            for blocker in value.blockers
        ),
        warnings=value.warnings,
    )


@router.get(
    "/{knowledge_version_id}/promotion-assessment",
    response_model=PromotionAssessmentResponse,
)
def promotion_assessment(
    knowledge_version_id: uuid.UUID,
    session: ReadSession,
) -> PromotionAssessmentResponse:
    try:
        return _assessment(KnowledgePromotionService(session).assess(knowledge_version_id))
    except LookupError as exc:
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        ) from exc
    except KnowledgePromotionError as exc:
        raise AdminSemanticApiError(
            422,
            type(exc).__name__,
            "invalid_promotion_request",
            str(exc),
        ) from exc


@router.post(
    "/{knowledge_version_id}/promote",
    response_model=KnowledgeVersionPromotionResponse,
)
def promote_knowledge_version(
    knowledge_version_id: uuid.UUID,
    body: KnowledgeVersionPromoteRequest,
    session: WriteSession,
) -> KnowledgeVersionPromotionResponse:
    service = KnowledgePromotionService(session)
    try:
        result = service.promote(
            knowledge_version_id,
            reviewer=body.reviewer_id,
            reason=body.reason,
            expected_knowledge_version=body.expected_knowledge_version,
        )
    except LookupError as exc:
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        ) from exc
    except KnowledgePromotionBlockedError as exc:
        detail = (
            "; ".join(
                f"{blocker.code}: {blocker.message}" for blocker in exc.assessment.blockers[:4]
            )
            or "La promoción fue bloqueada por el Promotion Gate."
        )
        raise AdminSemanticApiError(
            409,
            type(exc).__name__,
            "promotion_blocked",
            detail,
        ) from exc
    except KnowledgePromotionError as exc:
        raise AdminSemanticApiError(
            409,
            type(exc).__name__,
            "promotion_conflict",
            str(exc),
        ) from exc

    return KnowledgeVersionPromotionResponse(
        promotion_id=result.promotion_id,
        knowledge_version_id=result.knowledge_version_id,
        knowledge_version=result.knowledge_version,
        erp_id=result.erp_id,
        previous_active_version_id=result.previous_active_version_id,
        sync_jobs=result.sync_jobs,
        assessment=_assessment(result.assessment),
    )
