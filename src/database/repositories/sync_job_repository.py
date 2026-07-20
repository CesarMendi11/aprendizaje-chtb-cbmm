import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import SyncStatus, SyncTarget
from src.database.models import SyncJob


class SyncJobRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(
        self, *, version_id: uuid.UUID | None = None, status: SyncStatus | None = None
    ) -> list[SyncJob]:
        query = select(SyncJob)
        if version_id:
            query = query.where(SyncJob.knowledge_version_id == version_id)
        if status:
            query = query.where(SyncJob.status == status)
        return list(self.session.scalars(query.order_by(SyncJob.requested_at.desc())))

    def get(self, version_id: uuid.UUID, target: SyncTarget, *, for_update=False) -> SyncJob | None:
        query = select(SyncJob).where(
                SyncJob.knowledge_version_id == version_id, SyncJob.target == target
            )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)
