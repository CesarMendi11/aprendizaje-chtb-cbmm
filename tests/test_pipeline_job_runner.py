from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.base import Base
from src.database.enums import PipelineJobStatus
from src.database.repositories import PipelineJobRepository
from src.database.services import PipelineJobService
from src.pipeline.pipeline_job_runner import PipelineJobRunner


class FakeCrawlExecutor:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "screen_captured",
            {
                "work_units": 3,
                "routes_visited": 1,
                "states_explored": 2,
                "current_route": target,
            },
        )
        if self.fail:
            raise RuntimeError("fallo controlado del crawler")
        return {
            "run_id": str(job_id),
            "scope": str(getattr(scope, "value", scope)),
            "target": target,
            "functional_screens": 1,
        }


class FakeCanonicalBuildExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "exporting_canonical",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": "canonical-test",
            },
        )
        return {
            "knowledge_version": "canonical-test",
            "source_crawl_job_id": parameters["source_crawl_job_id"],
        }



class FakeCanonicalImportExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "staging_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": "staging-test",
            },
        )
        return {
            "knowledge_version": "staging-test",
            "source_canonical_job_id": parameters["source_canonical_job_id"],
            "staging_ready": True,
        }


class FakeProjectionSyncExecutor:
    def __init__(self, target):
        self.target = target
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            f"{self.target}_synced",
            {
                "work_units": 4,
                "progress_total": 4,
                "eligible_items": 21,
            },
        )
        return {
            "target": self.target,
            "active_only": True,
            "knowledge_version": parameters["knowledge_version"],
            "knowledge_version_id": parameters["knowledge_version_id"],
            "eligible_items": 21,
        }

def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_runner_executes_queued_crawl_and_persists_progress_and_result():
    engine, factory = build_factory()
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="crawl",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={"headless": True, "slow_mo": 0},
        )
        job_id = job.id

    executor = FakeCrawlExecutor()
    PipelineJobRunner(factory, crawl_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.stage == "completed"
        assert stored.progress_current == 3
        assert stored.checkpoint["routes_visited"] == 1
        assert stored.checkpoint["states_explored"] == 2
        assert stored.result_payload["functional_screens"] == 1
        assert stored.finished_at is not None
    assert len(executor.calls) == 1
    engine.dispose()


def test_runner_marks_failed_crawl_without_exposing_worker_exception_to_api_thread():
    engine, factory = build_factory()
    with factory.begin() as session:
        job = PipelineJobService(session).create(kind="crawl", scope="full")
        job_id = job.id

    PipelineJobRunner(factory, crawl_executor=FakeCrawlExecutor(fail=True)).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.FAILED
        assert stored.stage == "failed"
        assert "fallo controlado" in (stored.error_summary or "")
    engine.dispose()


def test_runner_dispatches_canonical_build_and_persists_total_progress():
    engine, factory = build_factory()
    source_id = "00000000-0000-0000-0000-000000000123"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="canonical_build",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={"source_crawl_job_id": source_id},
        )
        job_id = job.id

    executor = FakeCanonicalBuildExecutor()
    PipelineJobRunner(
        factory, canonical_build_executor=executor
    ).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == 4
        assert stored.progress_total == 4
        assert stored.result_payload["knowledge_version"] == "canonical-test"
        assert stored.checkpoint["knowledge_version"] == "canonical-test"
    assert len(executor.calls) == 1
    engine.dispose()


def test_runner_dispatches_canonical_import_executor():
    engine, factory = build_factory()
    source_id = "00000000-0000-0000-0000-000000000789"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="canonical_import",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={"source_canonical_job_id": source_id},
        )
        job_id = job.id

    executor = FakeCanonicalImportExecutor()
    PipelineJobRunner(factory, canonical_import_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == 4
        assert stored.progress_total == 4
        assert stored.result_payload["staging_ready"] is True
    assert len(executor.calls) == 1
    engine.dispose()



def test_runner_dispatches_projection_sync_executors():
    engine, factory = build_factory()
    version_id = "00000000-0000-0000-0000-000000000555"
    jobs = []
    with factory.begin() as session:
        for kind in ("neo4j_sync", "chroma_sync"):
            job = PipelineJobService(session).create(
                kind=kind,
                scope="version",
                target="active-v1",
                parameters={
                    "active_only": True,
                    "knowledge_version": "active-v1",
                    "knowledge_version_id": version_id,
                },
            )
            jobs.append((kind, job.id))

    neo = FakeProjectionSyncExecutor("neo4j")
    chroma = FakeProjectionSyncExecutor("chromadb")
    runner = PipelineJobRunner(
        factory, neo4j_sync_executor=neo, chroma_sync_executor=chroma
    )
    for _kind, job_id in jobs:
        runner.run(job_id)

    with factory() as session:
        for kind, job_id in jobs:
            stored = PipelineJobRepository(session).get(job_id)
            assert stored is not None
            assert stored.status == PipelineJobStatus.SUCCEEDED
            assert stored.progress_current == 4
            assert stored.progress_total == 4
            assert stored.result_payload["active_only"] is True
    assert len(neo.calls) == 1
    assert len(chroma.calls) == 1
    engine.dispose()
