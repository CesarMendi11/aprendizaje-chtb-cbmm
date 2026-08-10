from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from src.database.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus
from src.database.repositories import PipelineJobRepository
from src.database.services import PipelineJobService
from src.pipeline.crawl_job_executor import CrawlJobExecutor


@dataclass(frozen=True)
class CrawlJobSpec:
    id: uuid.UUID
    scope: PipelineJobScope
    target: str | None
    parameters: dict[str, Any]


class PipelineJobRunner:
    """Consume queued jobs and persist progress in short independent transactions."""

    def __init__(self, session_factory, *, crawl_executor: CrawlJobExecutor | None = None):
        self.session_factory = session_factory
        self.crawl_executor = crawl_executor or CrawlJobExecutor()

    def run(self, job_id: uuid.UUID | str) -> None:
        spec = self._start(job_id)
        if spec is None:
            return

        try:
            result = self.crawl_executor.execute(
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
            )

    def _start(self, job_id: uuid.UUID | str) -> CrawlJobSpec | None:
        with self.session_factory.begin() as session:
            job = PipelineJobRepository(session).get(job_id, for_update=True)
            if job is None or job.status != PipelineJobStatus.QUEUED:
                return None
            if job.kind != PipelineJobKind.CRAWL:
                service = PipelineJobService(session)
                service.start(job.id, stage="unsupported_job_kind")
                service.fail(job.id, "Este runner sólo ejecuta jobs de crawling")
                return None
            PipelineJobService(session).start(job.id, stage="starting")
            return CrawlJobSpec(
                id=job.id,
                scope=job.scope,
                target=job.target,
                parameters=dict(job.parameters or {}),
            )

    def _checkpoint(self, job_id: uuid.UUID, stage: str, payload: dict[str, Any]) -> None:
        current = int(payload.get("work_units", 0) or 0)
        checkpoint = {key: value for key, value in payload.items() if key != "work_units"}
        with self.session_factory.begin() as session:
            job = PipelineJobRepository(session).get(job_id)
            if job is None or job.status != PipelineJobStatus.RUNNING:
                return
            PipelineJobService(session).checkpoint(
                job_id,
                stage=stage,
                progress_current=max(0, current),
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
            # El crawler ya produjo artefactos aislados si alcanzó a iniciar.
            # No se oculta el error original dentro del worker ni se expone un secreto al cliente.
            return
