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
