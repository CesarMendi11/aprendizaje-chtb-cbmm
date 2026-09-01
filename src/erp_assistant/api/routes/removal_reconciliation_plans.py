from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from erp_assistant.api.dependencies import get_admin_read_session
from erp_assistant.api.routes.semantic_review import AdminSemanticApiError
from erp_assistant.api.schemas.removal_reconciliation_plan import (
    RemovalReconciliationDecisionResponse,
    RemovalReconciliationPlanResponse,
)
from erp_assistant.structural.services.removal_reconciliation_plan_service import (
    RemovalReconciliationPlanError,
    RemovalReconciliationPlanService,
)
from erp_assistant.structural.services.structural_review_package_service import (
    StructuralReviewPackageError,
)
from erp_assistant.structural.services.version_diff_service import VersionDiffError

router = APIRouter(prefix="/removal-reconciliation-plans", tags=["removal reconciliation plans"])
ReadSession = Annotated[Session, Depends(get_admin_read_session)]


@router.get("/{candidate_version_id}", response_model=RemovalReconciliationPlanResponse)
def removal_reconciliation_plan(candidate_version_id: uuid.UUID, session: ReadSession):
    try:
        value = RemovalReconciliationPlanService(session).build(candidate_version_id)
    except LookupError as exc:
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        ) from exc
    except (VersionDiffError, StructuralReviewPackageError, RemovalReconciliationPlanError) as exc:
        raise AdminSemanticApiError(
            422, type(exc).__name__, "invalid_removal_reconciliation_plan", str(exc)
        ) from exc
    return RemovalReconciliationPlanResponse(
        **{
            **value.__dict__,
            "decisions": tuple(
                RemovalReconciliationDecisionResponse(**item.__dict__) for item in value.decisions
            ),
        }
    )
