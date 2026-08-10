from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.database.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus


class PipelineJobSummary(BaseModel):
    id: uuid.UUID
    kind: PipelineJobKind
    status: PipelineJobStatus
    scope: PipelineJobScope
    target: str | None
    profile_name: str | None
    erp_id: str | None
    knowledge_version_id: uuid.UUID | None
    request_source: str
    stage: str
    progress_current: int
    progress_total: int | None
    progress_percent: float | None = Field(default=None, ge=0, le=100)
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_summary: str | None


class PipelineJobDetail(PipelineJobSummary):
    parameters: dict[str, Any]
    checkpoint: dict[str, Any]
    result_payload: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class PipelineJobListResponse(BaseModel):
    items: tuple[PipelineJobSummary, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class CrawlPipelineJobCreateRequest(BaseModel):
    scope: PipelineJobScope
    target: str | None = Field(default=None, max_length=1000)
    headless: bool = False
    slow_mo: int = Field(default=0, ge=0, le=5000)

    @model_validator(mode="after")
    def validate_crawl_scope(self):
        if self.scope not in {PipelineJobScope.FULL, PipelineJobScope.SCREEN}:
            raise ValueError("El crawler sólo admite scope=full o scope=screen")
        if self.scope == PipelineJobScope.SCREEN:
            target = (self.target or "").strip()
            if not target:
                raise ValueError("scope=screen requiere target")
            if not target.startswith("/") or "://" in target:
                raise ValueError("target debe ser una ruta interna del ERP")
            self.target = target
        elif self.target is not None:
            raise ValueError("scope=full no acepta target")
        return self


class CanonicalBuildPipelineJobCreateRequest(BaseModel):
    source_crawl_job_id: uuid.UUID


class CanonicalImportPipelineJobCreateRequest(BaseModel):
    source_canonical_job_id: uuid.UUID
