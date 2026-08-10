from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.base import Base
from src.database.enums import PipelineJobStatus
from src.database.services import (
    PipelineJobError,
    PipelineJobService,
    PipelineJobTransitionError,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as value:
        yield value
    engine.dispose()


def test_pipeline_job_lifecycle_and_progress(session):
    service = PipelineJobService(session)
    job = service.create(
        kind="crawl",
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        profile_name="cbmm",
        parameters={"headless": False},
    )
    assert job.status == PipelineJobStatus.QUEUED
    assert job.stage == "queued"
    assert job.progress_current == 0

    job = service.start(job.id, stage="discovering", progress_total=10)
    assert job.status == PipelineJobStatus.RUNNING
    assert job.started_at is not None

    job = service.checkpoint(
        job.id,
        stage="exploring_ui_states",
        progress_current=6,
        checkpoint={"states": 6},
    )
    assert job.progress_current == 6
    assert job.checkpoint == {"states": 6}

    job = service.succeed(job.id, result_payload={"screens": 1, "states": 8})
    assert job.status == PipelineJobStatus.SUCCEEDED
    assert job.progress_current == 10
    assert job.finished_at is not None
    assert job.result_payload == {"screens": 1, "states": 8}

    with pytest.raises(PipelineJobTransitionError):
        service.start(job.id)


def test_screen_scope_requires_target_and_progress_is_bounded(session):
    service = PipelineJobService(session)
    with pytest.raises(PipelineJobError, match="requiere target"):
        service.create(kind="crawl", scope="screen")

    job = service.create(kind="crawl", scope="full", profile_name="cbmm")
    service.start(job.id, progress_total=2)
    with pytest.raises(PipelineJobError, match="superar"):
        service.checkpoint(job.id, progress_current=3)


def test_failed_job_keeps_a_sanitized_terminal_record(session):
    service = PipelineJobService(session)
    job = service.create(kind="canonical_build", scope="full")
    service.start(job.id, stage="building")
    job = service.fail(job.id, "fallo sintético")
    assert job.status == PipelineJobStatus.FAILED
    assert job.stage == "failed"
    assert job.finished_at is not None
    assert "fallo sintético" in (job.error_summary or "")
