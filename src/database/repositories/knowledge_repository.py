from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus
from src.database.models import KnowledgeItem, KnowledgeVersionRecord
from src.knowledge.canonical.enums import ReviewStatus


class KnowledgeRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_version(self, erp_id: str, knowledge_version: str) -> KnowledgeVersionRecord | None:
        return self.session.scalar(
            select(KnowledgeVersionRecord).where(
                KnowledgeVersionRecord.erp_id == erp_id,
                KnowledgeVersionRecord.knowledge_version == knowledge_version,
            )
        )

    def get_active_version(self, erp_id: str) -> KnowledgeVersionRecord | None:
        return self.session.scalar(
            select(KnowledgeVersionRecord).where(
                KnowledgeVersionRecord.erp_id == erp_id,
                KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
            )
        )

    def list_versions(self, erp_id: str | None = None) -> list[KnowledgeVersionRecord]:
        query = select(KnowledgeVersionRecord)
        if erp_id:
            query = query.where(KnowledgeVersionRecord.erp_id == erp_id)
        return list(self.session.scalars(query.order_by(KnowledgeVersionRecord.imported_at.desc())))

    def get_item(self, item_id: uuid.UUID | str, *, for_update: bool = False) -> KnowledgeItem | None:
        query = select(KnowledgeItem).where(KnowledgeItem.id == uuid.UUID(str(item_id)))
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def get_item_by_identity(
        self,
        version_id: uuid.UUID,
        entity_type: str,
        canonical_id: str,
        *,
        for_update: bool = False,
    ) -> KnowledgeItem | None:
        query = select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == version_id,
            KnowledgeItem.entity_type == entity_type,
            KnowledgeItem.canonical_id == canonical_id,
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def list_items(
        self,
        *,
        version_id: uuid.UUID | None = None,
        status: ReviewStatus | str | None = None,
        entity_type: str | None = None,
        route: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeItem]:
        query = select(KnowledgeItem)
        if version_id:
            query = query.where(KnowledgeItem.knowledge_version_id == version_id)
        if status:
            query = query.where(KnowledgeItem.current_review_status == ReviewStatus(status))
        if entity_type:
            query = query.where(KnowledgeItem.entity_type == entity_type)
        if route:
            query = query.where(KnowledgeItem.route == route)
        query = query.order_by(KnowledgeItem.entity_type, KnowledgeItem.canonical_id)
        return list(self.session.scalars(query.offset(offset).limit(min(limit, 1000))))

