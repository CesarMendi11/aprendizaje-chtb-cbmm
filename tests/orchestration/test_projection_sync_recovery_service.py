from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from erp_assistant.api.app import create_app
from erp_assistant.config.api_settings import ApiSettings
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    SyncStatus,
    SyncTarget,
)
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeVersionRecord,
    PipelineJob,
    SyncJob,
)
from erp_assistant.orchestration.pipeline.recovery import ProjectionSyncRecoveryService


def _seed(session: Session):
    erp = ERPSystemRecord(
        id="erp:recovery",
        slug="recovery",
        name="ERP Recovery",
        profile_name="test",
        safe_metadata={},
    )
    run = ImportRun(
        erp=erp,
        source_knowledge_path="knowledge.json",
        source_manifest_path="manifest.json",
        requested_knowledge_version="active-recovery",
        status=ImportStatus.SUCCEEDED,
        source_hashes={},
    )
    version = KnowledgeVersionRecord(
        erp=erp,
        import_run=run,
        schema_version="1.1.0",
        knowledge_version="active-recovery",
        canonical_hash="a" * 64,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=KnowledgeVersionStatus.ACTIVE,
    )
    neo_sync = SyncJob(
        knowledge_version=version,
        target=SyncTarget.NEO4J,
        status=SyncStatus.PENDING,
        attempt_count=0,
    )
    chroma_sync = SyncJob(
        knowledge_version=version,
        target=SyncTarget.CHROMADB,
        status=SyncStatus.RUNNING,
        attempt_count=1,
        started_at=datetime.now(timezone.utc),
    )
    session.add_all([version, neo_sync, chroma_sync])
    session.flush()

    jobs = [
        PipelineJob(
            kind=PipelineJobKind.NEO4J_SYNC,
            status=PipelineJobStatus.QUEUED,
            scope=PipelineJobScope.VERSION,
            target=version.knowledge_version,
            erp_id=erp.id,
            knowledge_version_id=version.id,
            request_source="admin_api",
            parameters={},
            stage="queued",
            checkpoint={},
        ),
        PipelineJob(
            kind=PipelineJobKind.CHROMA_SYNC,
            status=PipelineJobStatus.RUNNING,
            scope=PipelineJobScope.VERSION,
            target=version.knowledge_version,
            erp_id=erp.id,
            knowledge_version_id=version.id,
            request_source="admin_api",
            parameters={},
            stage="embedding_and_syncing",
            checkpoint={},
            started_at=datetime.now(timezone.utc),
        ),
        PipelineJob(
            kind=PipelineJobKind.SEMANTIC_SYNC,
            status=PipelineJobStatus.RUNNING,
            scope=PipelineJobScope.VERSION,
            target=version.knowledge_version,
            erp_id=erp.id,
            knowledge_version_id=version.id,
            request_source="admin_api",
            parameters={},
            stage="embedding_and_syncing_semantics",
            checkpoint={},
            started_at=datetime.now(timezone.utc),
        ),
        PipelineJob(
            kind=PipelineJobKind.CRAWL,
            status=PipelineJobStatus.RUNNING,
            scope=PipelineJobScope.FULL,
            request_source="admin_api",
            parameters={},
            stage="crawling",
            checkpoint={},
            started_at=datetime.now(timezone.utc),
        ),
    ]
    session.add_all(jobs)
    session.flush()
    return neo_sync.id, chroma_sync.id, [job.id for job in jobs]


def test_recovery_fails_only_orphaned_projection_jobs_and_running_structural_sync():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            neo_sync_id, chroma_sync_id, job_ids = _seed(session)

        with session.begin():
            result = ProjectionSyncRecoveryService(session).recover_orphaned_jobs()

        assert result.pipeline_jobs_failed == 3
        assert result.sync_jobs_failed == 1

        jobs = {job.id: job for job in session.scalars(select(PipelineJob))}
        for job_id in job_ids[:3]:
            assert jobs[job_id].status == PipelineJobStatus.FAILED
            assert jobs[job_id].stage == "recovered_after_restart"
            assert "reinició" in (jobs[job_id].error_summary or "")
        assert jobs[job_ids[3]].status == PipelineJobStatus.RUNNING

        neo_sync = session.get(SyncJob, neo_sync_id)
        chroma_sync = session.get(SyncJob, chroma_sync_id)
        assert neo_sync.status == SyncStatus.PENDING
        assert chroma_sync.status == SyncStatus.FAILED
        assert "reinició" in (chroma_sync.error_summary or "")

    engine.dispose()


def test_recovery_is_idempotent_after_projection_jobs_are_failed():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            _seed(session)
        with session.begin():
            first = ProjectionSyncRecoveryService(session).recover_orphaned_jobs()
        with session.begin():
            second = ProjectionSyncRecoveryService(session).recover_orphaned_jobs()

        assert first.pipeline_jobs_failed == 3
        assert second.pipeline_jobs_failed == 0
        assert second.sync_jobs_failed == 0

    engine.dispose()


class _Dispatcher:
    def __init__(self):
        self.shutdown_calls = 0

    def submit(self, job_id):
        return None

    def shutdown(self):
        self.shutdown_calls += 1


def test_app_lifespan_recovers_projection_jobs_and_shuts_down_dispatcher(tmp_path):
    database_path = tmp_path / "projection-recovery.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        _, chroma_sync_id, job_ids = _seed(session)

    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    settings = replace(
        ApiSettings(),
        semantic_review_api_enabled=True,
    )
    dispatcher = _Dispatcher()
    app = create_app(
        settings,
        semantic_review_session_factory=factory,
        pipeline_job_dispatcher=dispatcher,
    )

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            assert app.state.projection_sync_recovery == {
                "pipeline_jobs_failed": 3,
                "sync_jobs_failed": 1,
            }
            with factory() as session:
                jobs = {job.id: job for job in session.scalars(select(PipelineJob))}
                assert jobs[job_ids[0]].status == PipelineJobStatus.FAILED
                assert jobs[job_ids[1]].status == PipelineJobStatus.FAILED
                assert jobs[job_ids[2]].status == PipelineJobStatus.FAILED
                assert jobs[job_ids[3]].status == PipelineJobStatus.RUNNING
                assert session.get(SyncJob, chroma_sync_id).status == SyncStatus.FAILED

    asyncio.run(run_lifespan())

    assert dispatcher.shutdown_calls == 1
    engine.dispose()
