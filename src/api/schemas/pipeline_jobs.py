from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

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
