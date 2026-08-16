from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import (
    PipelineJobKind,
    PipelineJobStatus,
    SyncStatus,
    SyncTarget,
)
from src.database.models import PipelineJob, SyncJob
from src.database.types import utcnow

PROJECTION_JOB_KINDS = {
    PipelineJobKind.NEO4J_SYNC,
    PipelineJobKind.CHROMA_SYNC,
    PipelineJobKind.SEMANTIC_SYNC,
}
STRUCTURAL_SYNC_TARGETS = {
    PipelineJobKind.NEO4J_SYNC: SyncTarget.NEO4J,
    PipelineJobKind.CHROMA_SYNC: SyncTarget.CHROMADB,
}


@dataclass(frozen=True)
class ProjectionSyncRecoveryResult:
    pipeline_jobs_failed: int
    sync_jobs_failed: int

    def as_dict(self) -> dict[str, int]:
        return {
            "pipeline_jobs_failed": self.pipeline_jobs_failed,
            "sync_jobs_failed": self.sync_jobs_failed,
        }


class ProjectionSyncRecoveryService:
    """Fail orphaned in-process projection jobs after an API process restart.

    PipelineJobDispatcher uses an in-memory queue and daemon thread. Therefore a
    projection job persisted as QUEUED/RUNNING before a fresh API startup cannot
    still be owned by the new process. We fail it explicitly so an operator can
    retry without leaving the projection lifecycle permanently blocked.
    """

    def __init__(self, session: Session):
        self.session = session

    def recover_orphaned_jobs(self) -> ProjectionSyncRecoveryResult:
        jobs = list(
            self.session.scalars(
                select(PipelineJob)
                .where(
                    PipelineJob.kind.in_(sorted(PROJECTION_JOB_KINDS)),
                    PipelineJob.status.in_(
                        [PipelineJobStatus.QUEUED, PipelineJobStatus.RUNNING]
                    ),
                )
                .order_by(PipelineJob.requested_at, PipelineJob.id)
            )
        )
        now = utcnow()
        recovered_sync_ids = set()
        recovered_sync_count = 0

        for job in jobs:
            previous_status = job.status
            job.status = PipelineJobStatus.FAILED
            job.stage = "recovered_after_restart"
            job.finished_at = now
            job.error_summary = (
                "La API se reinició antes de completar este job de proyección; "
                "puede reintentarse de forma segura."
            )

            target = STRUCTURAL_SYNC_TARGETS.get(job.kind)
            if (
                previous_status != PipelineJobStatus.RUNNING
                or target is None
                or job.knowledge_version_id is None
            ):
                continue

            sync_job = self.session.scalar(
                select(SyncJob)
                .where(
                    SyncJob.knowledge_version_id == job.knowledge_version_id,
                    SyncJob.target == target,
                )
                .with_for_update()
            )
            if (
                sync_job is None
                or sync_job.id in recovered_sync_ids
                or sync_job.status != SyncStatus.RUNNING
            ):
                continue
            sync_job.status = SyncStatus.FAILED
            sync_job.finished_at = now
            sync_job.error_summary = (
                "La API se reinició durante la sincronización; el intento quedó "
                "marcado como fallido y puede reintentarse."
            )
            recovered_sync_ids.add(sync_job.id)
            recovered_sync_count += 1

        self.session.flush()
        return ProjectionSyncRecoveryResult(
            pipeline_jobs_failed=len(jobs),
            sync_jobs_failed=recovered_sync_count,
        )
