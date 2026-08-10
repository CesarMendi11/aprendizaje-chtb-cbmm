from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.database.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus
from src.database.repositories import PipelineJobRepository
from src.database.services import PipelineJobService
from src.pipeline.canonical_build_job_executor import CanonicalBuildJobExecutor
from src.pipeline.canonical_import_job_executor import CanonicalImportJobExecutor
from src.pipeline.crawl_job_executor import CrawlJobExecutor


@dataclass(frozen=True)
class PipelineJobSpec:
    id: uuid.UUID
    kind: PipelineJobKind
    scope: PipelineJobScope
    target: str | None
    parameters: dict[str, Any]


class PipelineJobRunner:
    """Consume queued jobs and persist progress in short independent transactions."""

    def __init__(
        self,
        session_factory,
        *,
        crawl_executor: CrawlJobExecutor | None = None,
        canonical_build_executor: CanonicalBuildJobExecutor | None = None,
        canonical_import_executor: CanonicalImportJobExecutor | None = None,
    ):
        self.session_factory = session_factory
        self.executors = {
            PipelineJobKind.CRAWL: crawl_executor or CrawlJobExecutor(),
            PipelineJobKind.CANONICAL_BUILD: canonical_build_executor
            or CanonicalBuildJobExecutor(),
            PipelineJobKind.CANONICAL_IMPORT: canonical_import_executor
            or CanonicalImportJobExecutor(session_factory),
        }

    def run(self, job_id: uuid.UUID | str) -> None:
        spec = self._start(job_id)
        if spec is None:
            return

        executor = self.executors.get(spec.kind)
        if executor is None:
            self._fail(spec.id, RuntimeError(f"Job kind no soportado por el runner: {spec.kind}"))
            return

        try:
            result = executor.execute(
                job_id=spec.id,
                scope=spec.scope,
                target=spec.target,
                parameters=spec.parameters,
                progress=lambda stage, payload: self._checkpoint(spec.id, stage, payload),
            )
        except Exception as exc:
            self._fail(spec.id, exc)
            return

        with self.session_factory.begin() as session:
            PipelineJobService(session).succeed(
                spec.id,
                result_payload=result,
                stage="completed",
                erp_id=result.get("erp_id"),
                knowledge_version_id=result.get("knowledge_version_id"),
            )

    def _start(self, job_id: uuid.UUID | str) -> PipelineJobSpec | None:
        with self.session_factory.begin() as session:
            job = PipelineJobRepository(session).get(job_id, for_update=True)
            if job is None or job.status != PipelineJobStatus.QUEUED:
                return None
            PipelineJobService(session).start(job.id, stage="starting")
            return PipelineJobSpec(
                id=job.id,
                kind=job.kind,
                scope=job.scope,
                target=job.target,
                parameters=dict(job.parameters or {}),
            )

    def _checkpoint(self, job_id: uuid.UUID, stage: str, payload: dict[str, Any]) -> None:
        current = int(payload.get("work_units", 0) or 0)
        total_value = payload.get("progress_total")
        total = int(total_value) if total_value is not None else None
        checkpoint = {
            key: value
            for key, value in payload.items()
            if key not in {"work_units", "progress_total"}
        }
        with self.session_factory.begin() as session:
            job = PipelineJobRepository(session).get(job_id)
            if job is None or job.status != PipelineJobStatus.RUNNING:
                return
            PipelineJobService(session).checkpoint(
                job_id,
                stage=stage,
                progress_current=max(0, current),
                progress_total=total,
                checkpoint=checkpoint,
            )

    def _fail(self, job_id: uuid.UUID, error: Exception) -> None:
        try:
            with self.session_factory.begin() as session:
                job = PipelineJobRepository(session).get(job_id)
                if job is None or job.status != PipelineJobStatus.RUNNING:
                    return
                PipelineJobService(session).fail(job_id, error)
        except Exception:
            # El executor puede haber producido artefactos aislados si alcanzó a iniciar.
            # No se expone un error secundario del worker al hilo de la API.
            return
