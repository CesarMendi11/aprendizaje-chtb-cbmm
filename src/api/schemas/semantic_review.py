from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.analysis.schemas import ScreenPurposeInference
from src.knowledge.canonical.enums import ReviewStatus


class StrictResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SemanticProposalSummaryResponse(StrictResponseModel):
    semantic_id: str
    semantic_type: str
    current_review_status: ReviewStatus
    review_revision: int
    erp_id: str
    knowledge_version_id: str
    screen_id: str
    subject_title: str | None
    purpose_summary: str | None
    generation_model: str
    prompt_version: str
    evidence_hash: str
    created_at: datetime
    updated_at: datetime
    review_action_count: int


class SemanticProposalListResponse(StrictResponseModel):
    items: tuple[SemanticProposalSummaryResponse, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class ReviewActionResponse(StrictResponseModel):
    action: str
    previous_status: ReviewStatus
    new_status: ReviewStatus
    reason: str | None
    reviewer_id: str
    reviewer_identity_verified: Literal[False] = False
    corrected_payload: ScreenPurposeInference | None
    created_at: datetime


class ScreenEvidenceReviewResponse(StrictResponseModel):
    evidence_available: bool
    diagnostic: str | None = None
    screen_id: str | None = None
    screen_title: str | None = None
    screen_route: str | None = None
    module: dict[str, Any] | None = None
    fields: tuple[dict[str, Any], ...] = ()
    controls: tuple[dict[str, Any], ...] = ()
    tables: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    transitions: tuple[dict[str, Any], ...] = ()
    evidence_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_hash: str


class GenerationTraceResponse(StrictResponseModel):
    generation_model: str
    prompt_version: str
    prompt_hash: str
    generation_parameters: dict[str, Any]
    generation_parameters_hash: str
    source_content_hash: str


class SemanticProposalDetailResponse(StrictResponseModel):
    summary: SemanticProposalSummaryResponse
    source_payload: ScreenPurposeInference
    evidence: ScreenEvidenceReviewResponse
    evidence_ids: tuple[str, ...]
    generation_trace: GenerationTraceResponse
    review_history: tuple[ReviewActionResponse, ...]
    effective_payload: ScreenPurposeInference
    publishable: bool
    reviewer_identity_verified: Literal[False] = False


class EffectivePayloadResponse(StrictResponseModel):
    semantic_id: str
    current_review_status: ReviewStatus
    review_revision: int
    effective_payload: ScreenPurposeInference
    publishable_payload: ScreenPurposeInference | None
    reviewer_identity_verified: Literal[False] = False


class ReviewRequest(StrictResponseModel):
    reviewer_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=4000)
    expected_status: ReviewStatus
    expected_revision: int = Field(ge=0)

    @field_validator("reviewer_id", "reason")
    @classmethod
    def reject_markup(cls, value: str) -> str:
        clean = " ".join(value.split())
        lowered = clean.casefold()
        if "<" in clean or ">" in clean or "javascript:" in lowered or "<script" in lowered:
            raise ValueError("No se permite HTML ni texto ejecutable")
        return clean


class CorrectionRequest(ReviewRequest):
    corrected_payload: ScreenPurposeInference


class ReviewResultResponse(EffectivePayloadResponse):
    action: Literal["approve", "correct", "reject"]


class SemanticApiErrorResponse(StrictResponseModel):
    ok: Literal[False] = False
    error_class: str
    category: str
    semantic_id: str | None = None
    current_status: ReviewStatus | None = None
    detail: str
