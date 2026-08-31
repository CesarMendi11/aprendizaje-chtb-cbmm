from __future__ import annotations

from erp_assistant.api.schemas.version_promotion import StrictModel


class RemovalReconciliationDecisionResponse(StrictModel):
    entity_type: str
    canonical_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    screen_id: str | None
    reason: str
    decision: str
    removal_confirmation: str | None
    requires_human_review: bool
    review_set_id: str | None = None
    review_decision_id: str | None = None
    review_action_id: str | None = None
    review_revision: int | None = None


class RemovalReconciliationPlanResponse(StrictModel):
    candidate_version_id: str
    candidate_knowledge_version: str
    active_version_id: str
    active_knowledge_version: str
    candidate_origin: str
    raw_diff_totals: dict[str, int]
    removal_total: int
    retain_from_active_total: int
    confirmed_removed_total: int
    unresolved_total: int
    decisions: tuple[RemovalReconciliationDecisionResponse, ...]
