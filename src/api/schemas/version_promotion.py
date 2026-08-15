from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromotionBlockerResponse(StrictModel):
    code: str
    message: str
    count: int
    entity_type: str | None


class PromotionAssessmentResponse(StrictModel):
    knowledge_version_id: str
    knowledge_version: str
    erp_id: str
    version_status: str
    promotable: bool
    bootstrap_promotion: bool
    promotion_mode: str
    current_active_version_id: str | None
    current_active_knowledge_version: str | None
    required_entity_types: tuple[str, ...]
    required_review_counts: dict[str, dict[str, int]]
    all_review_counts: dict[str, int]
    replacement_review_counts: dict[str, int]
    diff_totals: dict[str, int] | None
    pipeline_import_job_id: str | None
    source_canonical_job_id: str | None
    source_reconciliation_job_id: str | None
    removal_review_set_id: str | None
    decision_set_hash: str | None
    build_warning_count: int
    blockers: tuple[PromotionBlockerResponse, ...]
    warnings: tuple[str, ...]


class KnowledgeVersionPromoteRequest(StrictModel):
    reviewer_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=4000)
    expected_knowledge_version: str = Field(min_length=1, max_length=120)
    confirm_promotion: Literal[True]

    @field_validator("reviewer_id", "reason", "expected_knowledge_version")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        clean = " ".join(value.split())
        lowered = clean.casefold()
        if "<" in clean or ">" in clean or "javascript:" in lowered or "<script" in lowered:
            raise ValueError("No se permite HTML ni texto ejecutable")
        return clean


class KnowledgeVersionPromotionResponse(StrictModel):
    promotion_id: str
    knowledge_version_id: str
    knowledge_version: str
    erp_id: str
    previous_active_version_id: str | None
    sync_jobs: dict[str, str]
    assessment: PromotionAssessmentResponse
