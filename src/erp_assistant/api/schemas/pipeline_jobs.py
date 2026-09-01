from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from erp_assistant.persistence.postgres.enums import (
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)


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
    target_module_id: str | None = Field(default=None, max_length=240)
    knowledge_version_id: uuid.UUID | None = None
    headless: bool = False
    slow_mo: int = Field(default=0, ge=0, le=5000)

    @model_validator(mode="after")
    def validate_crawl_scope(self):
        if self.scope not in {
            PipelineJobScope.FULL,
            PipelineJobScope.MODULE,
            PipelineJobScope.SCREEN,
        }:
            raise ValueError("El crawler sólo admite scope=full, scope=module o scope=screen")

        target = (self.target or "").strip()
        module_id = (self.target_module_id or "").strip()

        if self.scope == PipelineJobScope.SCREEN:
            if not target:
                raise ValueError("scope=screen requiere target")
            if not target.startswith("/") or "://" in target:
                raise ValueError("target debe ser una ruta interna del ERP")
            if module_id:
                raise ValueError("scope=screen no acepta target_module_id")
            self.target = target
            self.target_module_id = None
            return self

        if self.scope == PipelineJobScope.MODULE:
            if target:
                raise ValueError("scope=module no acepta target; use target_module_id")
            if not module_id:
                raise ValueError("scope=module requiere target_module_id")
            if not module_id.startswith("module:"):
                raise ValueError("target_module_id debe ser un identificador canónico de módulo")
            self.target = None
            self.target_module_id = module_id
            return self

        if target or module_id or self.knowledge_version_id is not None:
            raise ValueError(
                "scope=full no acepta target, target_module_id ni knowledge_version_id"
            )
        self.target = None
        self.target_module_id = None
        return self


class CanonicalBuildPipelineJobCreateRequest(BaseModel):
    source_crawl_job_id: uuid.UUID


class CanonicalMergePipelineJobCreateRequest(BaseModel):
    source_canonical_job_id: uuid.UUID


class CanonicalReconciliationPipelineJobCreateRequest(BaseModel):
    candidate_version_id: uuid.UUID


class CanonicalImportPipelineJobCreateRequest(BaseModel):
    source_canonical_job_id: uuid.UUID | None = None
    source_reconciliation_job_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_source(self):
        supplied = [
            self.source_canonical_job_id is not None,
            self.source_reconciliation_job_id is not None,
        ]
        if sum(supplied) != 1:
            raise ValueError(
                "canonical_import requiere exactamente un source canonical o reconciliation"
            )
        return self


class Neo4jSyncPipelineJobCreateRequest(BaseModel):
    batch_size: int = Field(default=200, ge=1, le=2000)
    replace_version: bool = False


class ChromaSyncPipelineJobCreateRequest(BaseModel):
    pass


class SemanticSyncPipelineJobCreateRequest(BaseModel):
    pass


class SemanticInferencePipelineJobCreateRequest(BaseModel):
    screen_id: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_screen_id(self):
        clean = self.screen_id.strip()
        if not clean.startswith("screen:"):
            raise ValueError("screen_id debe ser un identificador canónico de pantalla")
        self.screen_id = clean
        return self
