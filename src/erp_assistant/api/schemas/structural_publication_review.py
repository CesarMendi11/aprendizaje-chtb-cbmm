from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from erp_assistant.api.schemas.version_promotion import StrictModel


class StructuralPublicationReviewItemResponse(StrictModel):
    item_id: str
    entity_type: str
    canonical_id: str
    title: str | None
    route: str | None
    review_status: str
    review_revision: int
    content_hash: str


class StructuralPublicationReviewPackageResponse(StrictModel):
    scope_type: Literal["screen", "module", "system", "unscoped"]
    scope_id: str
    title: str | None
    route: str | None
    module_id: str | None
    module_path: tuple[str, ...]
    status_counts: dict[str, int]
    entity_counts: dict[str, int]
    pending_count: int
    publishable_count: int
    rejected_count: int
    review_required: bool
    package_hash: str
    review_items: tuple[StructuralPublicationReviewItemResponse, ...]


class StructuralPublicationReviewSummaryResponse(StrictModel):
    knowledge_version_id: str
    knowledge_version: str
    erp_id: str
    version_status: str
    status_counts: dict[str, int]
    publishable_count: int
    pending_count: int
    rejected_count: int
    package_count: int
    packages: tuple[StructuralPublicationReviewPackageResponse, ...]
    total: int
    limit: int | None
    offset: int
    next_offset: int | None


class StructuralPublicationApproveRequest(StrictModel):
    scope_type: Literal["screen", "module", "system", "unscoped"]
    scope_id: str = Field(min_length=1, max_length=200)
    expected_package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("scope_id", "reviewer_id", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        clean = " ".join(value.split())
        lowered = clean.casefold()
        if "<" in clean or ">" in clean or "javascript:" in lowered or "<script" in lowered:
            raise ValueError("No se permite HTML ni texto ejecutable")
        return clean


class StructuralPublicationApprovalResponse(StrictModel):
    approved_count: int
    package: StructuralPublicationReviewPackageResponse
