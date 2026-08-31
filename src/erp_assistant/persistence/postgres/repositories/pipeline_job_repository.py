from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus
from erp_assistant.persistence.postgres.models import PipelineJob


class PipelineJobRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, job: PipelineJob) -> PipelineJob:
        self.session.add(job)
        return job

    def get(
        self, job_id: uuid.UUID | str, *, for_update: bool = False
    ) -> PipelineJob | None:
        try:
            normalized = uuid.UUID(str(job_id))
        except (TypeError, ValueError):
            return None
        query = select(PipelineJob).where(PipelineJob.id == normalized)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)


    def find_active_projection_job(
        self,
        *,
        kind: PipelineJobKind | str,
        knowledge_version_id: uuid.UUID | str,
    ) -> PipelineJob | None:
        """Return the newest queued/running projection job for kind + version."""
        try:
            version_id = uuid.UUID(str(knowledge_version_id))
        except (TypeError, ValueError):
            return None
        return self.session.scalar(
            select(PipelineJob)
            .where(
                PipelineJob.kind == PipelineJobKind(kind),
                PipelineJob.knowledge_version_id == version_id,
                PipelineJob.status.in_(
                    [PipelineJobStatus.QUEUED, PipelineJobStatus.RUNNING]
                ),
            )
            .order_by(PipelineJob.requested_at.desc(), PipelineJob.id.desc())
            .limit(1)
        )

    def list_page(
        self,
        *,
        kind: PipelineJobKind | str | None = None,
        status: PipelineJobStatus | str | None = None,
        scope: PipelineJobScope | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PipelineJob], int]:
        filters = []
        if kind is not None:
            filters.append(PipelineJob.kind == PipelineJobKind(kind))
        if status is not None:
            filters.append(PipelineJob.status == PipelineJobStatus(status))
        if scope is not None:
            filters.append(PipelineJob.scope == PipelineJobScope(scope))
        total = self.session.scalar(
            select(func.count()).select_from(PipelineJob).where(*filters)
        ) or 0
        rows = list(
            self.session.scalars(
                select(PipelineJob)
                .where(*filters)
                .order_by(PipelineJob.requested_at.desc(), PipelineJob.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        return rows, int(total)
