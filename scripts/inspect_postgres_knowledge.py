from __future__ import annotations

from sqlalchemy import func, select

from src.database.enums import KnowledgeVersionStatus
from src.database.models import (
    ERPSystemRecord, ImportRun, KnowledgeItem, KnowledgeVersionRecord, SyncJob
)
from src.database.session import session_scope
from src.knowledge.canonical.enums import ReviewStatus

from .database_common import database_engine, print_json


def inspect(session):
    def grouped(column):
        return {str(k): v for k, v in session.execute(
            select(column, func.count()).select_from(KnowledgeItem).group_by(column)
        ).all()}
    active = list(session.scalars(select(KnowledgeVersionRecord).where(
        KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE
    )))
    return {
        "erp": [
            {"id": x.id, "slug": x.slug, "name": x.name}
            for x in session.scalars(select(ERPSystemRecord))
        ],
        "versions": [
            {"version": x.knowledge_version, "status": str(x.status)}
            for x in session.scalars(select(KnowledgeVersionRecord))
        ],
        "active": [x.knowledge_version for x in active],
        "items_by_entity_type": grouped(KnowledgeItem.entity_type),
        "items_by_review_status": grouped(KnowledgeItem.current_review_status),
        "routes_without_module": session.scalar(
            select(func.count()).select_from(KnowledgeItem).where(
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.parent_canonical_id.is_(None),
            )
        ),
        "corrected_items": session.scalar(
            select(func.count()).select_from(KnowledgeItem).where(
                KnowledgeItem.current_review_status == ReviewStatus.CORRECTED
            )
        ),
        "importations": session.scalar(select(func.count()).select_from(ImportRun)),
        "sync_jobs": session.scalar(select(func.count()).select_from(SyncJob)),
    }


def main():
    try:
        with session_scope(database_engine()) as session:
            print_json(inspect(session), pretty=True)
        return 0
    except Exception as exc:
        print_json({"status": "error", "error": str(exc)[:500]}, pretty=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
