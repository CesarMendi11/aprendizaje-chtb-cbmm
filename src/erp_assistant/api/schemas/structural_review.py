from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from erp_assistant.structural.canonical.enums import ReviewStatus


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructuralReviewItemSummary(StrictResponseModel):
    id: str
    canonical_id: str
    entity_type: str
    parent_canonical_id: str | None
    title: str | None
    route: str | None
    current_review_status: ReviewStatus
    generated_review_status: ReviewStatus
    review_revision: int
    knowledge_version_id: str
    knowledge_version: str
    version_status: str
    content_hash: str
    created_at: datetime
    updated_at: datetime


class StructuralReviewListResponse(StrictResponseModel):
    items: tuple[StructuralReviewItemSummary, ...]
    status_counts: dict[str, int]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class StructuralReviewActionResponse(StrictResponseModel):
    action: str
    previous_status: ReviewStatus
    new_status: ReviewStatus
    source: str
    reviewer_id: str | None
    reason: str | None
    corrected_payload: dict[str, Any] | None
    created_at: datetime


class StructuralReviewItemDetail(StructuralReviewItemSummary):
    source_payload: dict[str, Any]
    corrected_payload: dict[str, Any] | None
    effective_payload: dict[str, Any]
    was_corrected: bool
    review_history: tuple[StructuralReviewActionResponse, ...]
    reviewer_identity_verified: Literal[False] = False


class StructuralReviewRequest(StrictResponseModel):
    reviewer_id: str = Field(min_length=1, max_length=240)
    reason: str | None = Field(default=None, max_length=4000)
    expected_status: ReviewStatus
    expected_revision: int = Field(ge=0)

    @field_validator("reviewer_id", "reason")
    @classmethod
    def reject_markup(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = " ".join(value.split())
        lowered = clean.casefold()
        if "<" in clean or ">" in clean or "javascript:" in lowered or "<script" in lowered:
            raise ValueError("No se permite HTML ni texto ejecutable")
        return clean


class StructuralCorrectionRequest(StructuralReviewRequest):
    reason: str = Field(min_length=1, max_length=4000)
    corrected_payload: dict[str, Any]


class StructuralReviewResultResponse(StructuralReviewItemDetail):
    performed_action: Literal["approve", "correct", "reject", "reset_to_pending"]
