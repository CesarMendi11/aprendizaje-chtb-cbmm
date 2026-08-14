from __future__ import annotations

from src.api.schemas.version_promotion import StrictModel


class StructuralReviewChangeResponse(StrictModel):
    change_type: str
    entity_type: str
    canonical_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    removal_confirmation: str | None
    requires_removal_review: bool


class StructuralScreenReviewPackageResponse(StrictModel):
    screen_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    title: str | None
    route: str | None
    module_id: str | None
    module_path: tuple[str, ...]
    change_type: str
    active_review_status: str | None
    candidate_review_status: str | None
    carry_forward: bool | None
    counts: dict[str, int]
    unconfirmed_removals: int
    review_required: bool
    changes: tuple[StructuralReviewChangeResponse, ...]


class StructuralReviewPackagesResponse(StrictModel):
    active_version_id: str
    active_knowledge_version: str
    candidate_version_id: str
    candidate_knowledge_version: str
    erp_id: str
    candidate_origin: str
    diff_totals: dict[str, int]
    affected_screens: int
    screens_with_changes: int
    screens_unchanged: int
    unconfirmed_removals: int
    unscoped_changes: tuple[StructuralReviewChangeResponse, ...]
    packages: tuple[StructuralScreenReviewPackageResponse, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None
