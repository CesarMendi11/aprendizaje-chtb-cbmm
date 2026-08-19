from __future__ import annotations

import uuid

from src.database.enums import SyncStatus, SyncTarget
from src.database.repositories import SyncJobRepository
from src.database.types import utcnow
from src.knowledge.canonical.privacy import sanitize_text


def sync_attempt_count(session_factory, version_id: uuid.UUID, target: SyncTarget) -> int | None:
    with session_factory() as session:
        job = SyncJobRepository(session).get(version_id, target)
        return job.attempt_count if job is not None else None


def fail_preflight_sync(
    session_factory,
    *,
    version_id: uuid.UUID,
    target: SyncTarget,
    attempt_count_before: int | None,
    error: Exception,
) -> None:
    clean, _ = sanitize_text(str(error), 400)
    with session_factory.begin() as session:
        job = SyncJobRepository(session).get(version_id, target, for_update=True)
        if job is None:
            return
        now = utcnow()
        if attempt_count_before is not None and job.attempt_count <= attempt_count_before:
            job.attempt_count = attempt_count_before + 1
            job.started_at = now
        job.status = SyncStatus.FAILED
        job.finished_at = now
        job.error_summary = clean or "Error de preparación de proyección sanitizado"
        session.flush()
