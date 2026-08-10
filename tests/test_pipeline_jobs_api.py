from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.services import PipelineJobService


class Client:
    def __init__(self, app):
        self.app = app

    def get(self, path):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=self.app, client=("127.0.0.1", 50000)
                ),
                base_url="http://test",
            ) as client:
                return await client.get(path)

        return asyncio.run(send())


@pytest.fixture
def api(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    settings = replace(
        ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True
    )
    database_path = tmp_path / "pipeline_jobs.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, semantic_review_session_factory=factory)
    yield Client(app), factory
    engine.dispose()
    database_path.unlink(missing_ok=True)


def seed(factory):
    with factory.begin() as session:
        service = PipelineJobService(session)
        first = service.create(
            kind="crawl",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            profile_name="cbmm",
        )
        first_id = first.id
        second = service.create(kind="canonical_build", scope="full")
        second_id = second.id
        service.start(second.id, stage="building", progress_total=4)
        service.checkpoint(second.id, progress_current=2)
    return first_id, second_id


def test_pipeline_job_list_filters_and_detail(api):
    client, factory = api
    first_id, second_id = seed(factory)

    response = client.get("/api/admin/pipeline-jobs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    running = client.get("/api/admin/pipeline-jobs?status=running")
    assert running.status_code == 200
    assert running.json()["total"] == 1
    assert running.json()["items"][0]["id"] == str(second_id)
    assert running.json()["items"][0]["progress_percent"] == 50.0

    detail = client.get(f"/api/admin/pipeline-jobs/{first_id}")
    assert detail.status_code == 200
    assert detail.json()["kind"] == "crawl"
    assert detail.json()["scope"] == "screen"
    assert detail.json()["target"].endswith("/retenciones")


def test_pipeline_job_not_found_and_validation(api):
    client, _ = api
    missing = client.get("/api/admin/pipeline-jobs/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert client.get("/api/admin/pipeline-jobs?status=unknown").status_code == 422


def test_pipeline_job_api_is_hidden_when_admin_api_is_disabled(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    app = create_app(
        replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=False)
    )
    client = Client(app)
    assert client.get("/api/admin/pipeline-jobs").status_code == 404
