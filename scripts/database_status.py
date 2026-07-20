from __future__ import annotations

from sqlalchemy import func, select, text

from src.database.enums import KnowledgeVersionStatus, SyncStatus
from src.database.models import (
    ERPSystemRecord, ImportRun, KnowledgeItem, KnowledgeVersionRecord, SyncJob
)
from src.database.session import session_scope

from .database_common import database_engine, print_json


def collect_status(session):
    erps = list(session.scalars(select(ERPSystemRecord)))
    versions = list(session.scalars(select(KnowledgeVersionRecord)))
    status_counts = dict(
        session.execute(
            select(KnowledgeItem.current_review_status, func.count()).group_by(
                KnowledgeItem.current_review_status
            )
        ).all()
    )
    latest = session.scalar(select(ImportRun).order_by(ImportRun.started_at.desc()).limit(1))
    return {
        "connectivity": session.scalar(select(text("'ok'"))),
        "migration_version": session.scalar(text("SELECT version_num FROM alembic_version")),
        "erp_systems": len(erps),
        "versions": len(versions),
        "active_versions": [
            item.knowledge_version for item in versions
            if item.status == KnowledgeVersionStatus.ACTIVE
        ],
        "items_by_status": {str(key): value for key, value in status_counts.items()},
        "last_import_run": {
            "id": str(latest.id), "status": str(latest.status)
        } if latest else None,
        "pending_sync_jobs": session.scalar(
            select(func.count()).select_from(SyncJob).where(SyncJob.status == SyncStatus.PENDING)
        ),
    }


def main():
    try:
        with session_scope(database_engine()) as session:
            print_json(collect_status(session), pretty=True)
        return 0
    except Exception as exc:
        print_json({"connectivity": "error", "error": str(exc)[:500]}, pretty=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
