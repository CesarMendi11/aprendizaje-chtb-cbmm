from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.database.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus
from src.database.models import PipelineJob
from src.database.repositories import PipelineJobRepository
from src.database.types import utcnow
from src.knowledge.canonical.privacy import sanitize_text


class PipelineJobError(ValueError):
    pass


class PipelineJobNotFoundError(PipelineJobError):
    pass


class PipelineJobTransitionError(PipelineJobError):
    pass


class PipelineJobService:
    def __init__(self, session: Session):
        self.session = session
        self.jobs = PipelineJobRepository(session)

    def create(
        self,
        *,
        kind: PipelineJobKind | str,
        scope: PipelineJobScope | str,
        target: str | None = None,
        profile_name: str | None = None,
        erp_id: str | None = None,
        knowledge_version_id: uuid.UUID | None = None,
        request_source: str = "admin_api",
        parameters: dict[str, Any] | None = None,
    ) -> PipelineJob:
        normalized_kind = PipelineJobKind(kind)
        normalized_scope = PipelineJobScope(scope)
        clean_target = target.strip() if target else None
        if normalized_scope in {PipelineJobScope.MODULE, PipelineJobScope.SCREEN} and not clean_target:
            raise PipelineJobError(f"scope={normalized_scope.value} requiere target")
        clean_source = request_source.strip()
        if not clean_source:
            raise PipelineJobError("request_source no puede estar vacío")
        job = PipelineJob(
            kind=normalized_kind,
            status=PipelineJobStatus.QUEUED,
            scope=normalized_scope,
            target=clean_target,
            profile_name=profile_name.strip() if profile_name else None,
            erp_id=erp_id,
            knowledge_version_id=knowledge_version_id,
            request_source=clean_source,
            parameters=dict(parameters or {}),
            stage="queued",
            progress_current=0,
            progress_total=None,
            checkpoint={},
        )
        self.jobs.add(job)
        self.session.flush()
        self.session.refresh(job)
        return job

    def start(
        self,
        job_id: uuid.UUID | str,
        *,
        stage: str = "running",
        progress_total: int | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> PipelineJob:
        job = self._locked(job_id)
        self._require(job, {PipelineJobStatus.QUEUED}, PipelineJobStatus.RUNNING)
        if progress_total is not None and progress_total < 0:
            raise PipelineJobError("progress_total no puede ser negativo")
        job.status = PipelineJobStatus.RUNNING
        job.stage = self._stage(stage)
        job.started_at = utcnow()
        job.finished_at = None
        job.error_summary = None
        job.progress_current = 0
        job.progress_total = progress_total
        job.checkpoint = dict(checkpoint or {})
        return self._save(job)

    def checkpoint(
        self,
        job_id: uuid.UUID | str,
        *,
        stage: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> PipelineJob:
        job = self._locked(job_id)
        self._require(job, {PipelineJobStatus.RUNNING}, PipelineJobStatus.RUNNING)
        total = job.progress_total if progress_total is None else progress_total
        current = job.progress_current if progress_current is None else progress_current
        if current < 0 or (total is not None and total < 0):
            raise PipelineJobError("El progreso no puede ser negativo")
        if total is not None and current > total:
            raise PipelineJobError("progress_current no puede superar progress_total")
        if stage is not None:
            job.stage = self._stage(stage)
        job.progress_current = current
        job.progress_total = total
        if checkpoint is not None:
            job.checkpoint = dict(checkpoint)
        return self._save(job)

    def succeed(
        self,
        job_id: uuid.UUID | str,
        *,
        result_payload: dict[str, Any] | None = None,
        stage: str = "completed",
        erp_id: str | None = None,
        knowledge_version_id: uuid.UUID | str | None = None,
    ) -> PipelineJob:
        job = self._locked(job_id)
        self._require(job, {PipelineJobStatus.RUNNING}, PipelineJobStatus.SUCCEEDED)
        job.status = PipelineJobStatus.SUCCEEDED
        job.stage = self._stage(stage)
        job.finished_at = utcnow()
        if job.progress_total is not None:
            job.progress_current = job.progress_total
        job.result_payload = dict(result_payload or {})
        if erp_id is not None:
            job.erp_id = erp_id
        if knowledge_version_id is not None:
            job.knowledge_version_id = uuid.UUID(str(knowledge_version_id))
        job.error_summary = None
        return self._save(job)

    def fail(
        self,
        job_id: uuid.UUID | str,
        error: str | Exception,
        *,
        stage: str = "failed",
    ) -> PipelineJob:
        job = self._locked(job_id)
        self._require(job, {PipelineJobStatus.RUNNING}, PipelineJobStatus.FAILED)
        clean, _ = sanitize_text(str(error), 480)
        job.status = PipelineJobStatus.FAILED
        job.stage = self._stage(stage)
        job.finished_at = utcnow()
        job.error_summary = clean or "Error del pipeline sanitizado"
        return self._save(job)

    def cancel(
        self, job_id: uuid.UUID | str, *, stage: str = "cancelled"
    ) -> PipelineJob:
        job = self._locked(job_id)
        self._require(
            job,
            {PipelineJobStatus.QUEUED, PipelineJobStatus.RUNNING},
            PipelineJobStatus.CANCELLED,
        )
        job.status = PipelineJobStatus.CANCELLED
        job.stage = self._stage(stage)
        job.finished_at = utcnow()
        return self._save(job)

    def _locked(self, job_id: uuid.UUID | str) -> PipelineJob:
        job = self.jobs.get(job_id, for_update=True)
        if job is None:
            raise PipelineJobNotFoundError("PipelineJob no encontrado")
        return job

    @staticmethod
    def _require(
        job: PipelineJob, allowed: set[PipelineJobStatus], target: PipelineJobStatus
    ) -> None:
        if job.status not in allowed:
            raise PipelineJobTransitionError(
                f"Transición {job.status} -> {target} no permitida"
            )

    @staticmethod
    def _stage(value: str) -> str:
        clean = value.strip()
        if not clean:
            raise PipelineJobError("stage no puede estar vacío")
        if len(clean) > 120:
            raise PipelineJobError("stage excede 120 caracteres")
        return clean

    def _save(self, job: PipelineJob) -> PipelineJob:
        self.session.flush()
        self.session.refresh(job)
        return job
