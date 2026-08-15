from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from src.api.schemas.version_promotion import StrictModel


class RemovalReviewDecisionResponse(StrictModel):
    id: str
    entity_type: str
    canonical_id: str
    active_item_id: str
    candidate_item_id: str | None
    screen_id: str | None
    plan_reason: str
    removal_confirmation: str | None
    proposed_decision: str
    current_decision: str | None
    requires_human_review: bool
    review_revision: int
    decision_fingerprint: str


class RemovalReviewSetResponse(StrictModel):
    id: str
    candidate_version_id: str
    candidate_knowledge_version: str
    active_version_id: str
    active_knowledge_version: str
    erp_id: str
    candidate_origin: str
    raw_diff_totals: dict[str, int]
    plan_hash: str
    decision_count: int
    pending_review: int
    retain_from_active: int
    confirmed_remove: int
    decisions: tuple[RemovalReviewDecisionResponse, ...]


class RemovalReviewRequest(StrictModel):
    reviewer_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=0)

    @field_validator("reviewer_id", "reason")
    @classmethod
    def normalize_safe_text(cls, value: str) -> str:
        clean = " ".join(value.split())
        lowered = clean.casefold()
        if "<" in clean or ">" in clean or "javascript:" in lowered or "<script" in lowered:
            raise ValueError("No se permite HTML ni texto ejecutable")
        return clean


class RemovalReviewResultResponse(RemovalReviewDecisionResponse):
    performed_action: Literal["confirm_retain", "confirm_remove", "reset_to_pending"]


class RemovalReviewActionResponse(StrictModel):
    id: str
    action: str
    previous_decision: str | None
    new_decision: str | None
    review_notes: str
    reviewer_subject: str
    source: str
    decision_fingerprint: str
    created_at: datetime


class RemovalReviewHistoryResponse(StrictModel):
    decision_id: str
    actions: tuple[RemovalReviewActionResponse, ...]
