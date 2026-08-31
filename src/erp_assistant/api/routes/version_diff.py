from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from erp_assistant.api.dependencies import get_admin_read_session
from erp_assistant.api.routes.semantic_review import AdminSemanticApiError
from erp_assistant.api.schemas.version_diff import VersionDiffItemResponse, VersionDiffResponse
from erp_assistant.structural.services.version_diff_service import (
    VersionDiffChangeType,
    VersionDiffError,
    VersionDiffService,
)

router = APIRouter(prefix="/knowledge-versions", tags=["knowledge version diff"])
ReadSession = Annotated[Session, Depends(get_admin_read_session)]


@router.get("/{candidate_version_id}/diff", response_model=VersionDiffResponse)
def version_diff(
    candidate_version_id: uuid.UUID,
    session: ReadSession,
    change_type: VersionDiffChangeType | None = None,
    entity_type: str | None = Query(default=None, min_length=1, max_length=60),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> VersionDiffResponse:
    try:
        full = VersionDiffService(session).compare(candidate_version_id)
        page = VersionDiffService(session).compare(
            candidate_version_id,
            change_type=change_type,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
        )
    except LookupError as exc:
        raise AdminSemanticApiError(
            404,
            "KnowledgeVersionNotFoundError",
            "not_found",
            "Versión de conocimiento no encontrada.",
        ) from exc
    except VersionDiffError as exc:
        raise AdminSemanticApiError(
            422, type(exc).__name__, "invalid_version_diff", str(exc)
        ) from exc
    filtered_total = sum(
        1
        for item in full.items
        if (change_type is None or item.change_type == change_type)
        and (entity_type is None or item.entity_type == entity_type)
    )
    return VersionDiffResponse(
        active_version_id=page.active_version_id,
        active_knowledge_version=page.active_knowledge_version,
        candidate_version_id=page.candidate_version_id,
        candidate_knowledge_version=page.candidate_knowledge_version,
        erp_id=page.erp_id,
        totals=page.totals,
        counts_by_entity_type=page.counts_by_entity_type,
        items=tuple(VersionDiffItemResponse(**item.__dict__) for item in page.items),
        total=filtered_total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(page.items) if offset + len(page.items) < filtered_total else None,
    )
