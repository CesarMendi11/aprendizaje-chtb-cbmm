from __future__ import annotations

from erp_assistant.api.schemas.version_promotion import StrictModel


class VersionDiffItemResponse(StrictModel):
    change_type: str
    entity_type: str
    canonical_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    active_content_hash: str | None
    candidate_content_hash: str | None
    active_review_status: str | None
    candidate_review_status: str | None
    active_title: str | None
    candidate_title: str | None
    active_route: str | None
    candidate_route: str | None


class VersionDiffResponse(StrictModel):
    active_version_id: str
    active_knowledge_version: str
    candidate_version_id: str
    candidate_knowledge_version: str
    erp_id: str
    totals: dict[str, int]
    counts_by_entity_type: dict[str, dict[str, int]]
    items: tuple[VersionDiffItemResponse, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None
