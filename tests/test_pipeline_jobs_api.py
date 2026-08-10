from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import ERPSystemRecord, ImportRun, KnowledgeVersionRecord
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

    def post(self, path, json):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=self.app, client=("127.0.0.1", 50000)
                ),
                base_url="http://test",
            ) as client:
                return await client.post(path, json=json)

        return asyncio.run(send())


class FakeDispatcher:
    def __init__(self):
        self.submitted = []

    def submit(self, job_id):
        self.submitted.append(job_id)

    def shutdown(self):
        return None


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
    dispatcher = FakeDispatcher()
    app = create_app(
        settings,
        semantic_review_session_factory=factory,
        pipeline_job_dispatcher=dispatcher,
    )
    yield Client(app), factory, dispatcher
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
    client, factory, _ = api
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
    client, _, _ = api
    missing = client.get("/api/admin/pipeline-jobs/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert client.get("/api/admin/pipeline-jobs?status=unknown").status_code == 422


def test_create_crawl_job_queues_controlled_worker(api):
    client, factory, dispatcher = api
    response = client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={
            "scope": "screen",
            "target": "/admin/cuentasxcobrar/retenciones",
            "headless": False,
            "slow_mo": 120,
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "crawl"
    assert body["scope"] == "screen"
    assert body["status"] == "queued"
    assert body["parameters"] == {"headless": False, "slow_mo": 120}
    assert [str(value) for value in dispatcher.submitted] == [body["id"]]

    with factory() as session:
        stored = PipelineJobService(session).jobs.get(body["id"])
        assert stored is not None
        assert stored.target == "/admin/cuentasxcobrar/retenciones"


def test_create_full_crawl_rejects_target_and_screen_requires_internal_route(api):
    client, _, dispatcher = api
    assert client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={"scope": "full", "target": "/admin/retenciones"},
    ).status_code == 422
    assert client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={"scope": "screen", "target": "https://example.test/admin/x"},
    ).status_code == 422
    assert dispatcher.submitted == []


def test_pipeline_job_api_is_hidden_when_admin_api_is_disabled(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    app = create_app(
        replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=False)
    )
    client = Client(app)
    assert client.get("/api/admin/pipeline-jobs").status_code == 404


def test_create_canonical_build_job_requires_succeeded_crawl(api):
    client, factory, dispatcher = api
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="crawl",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            profile_name="cbmm",
        )
        source_id = source.id
        service.start(source.id, stage="running")
        service.succeed(
            source.id,
            result_payload={"artifact_root": f"data/runs/pipeline/{source.id}"},
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-build",
        json={"source_crawl_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_build"
    assert body["scope"] == "screen"
    assert body["target"] == "/admin/cuentasxcobrar/retenciones"
    assert body["parameters"]["source_crawl_job_id"] == str(source_id)
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_build_rejects_non_succeeded_source(api):
    client, factory, dispatcher = api
    with factory.begin() as session:
        source = PipelineJobService(session).create(kind="crawl", scope="full")
        source_id = source.id

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-build",
        json={"source_crawl_job_id": str(source_id)},
    )
    assert response.status_code == 409
    assert dispatcher.submitted == []


def test_create_canonical_import_job_is_staging_only(api):
    client, factory, dispatcher = api
    crawl_id = "00000000-0000-0000-0000-000000000456"
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            profile_name="cbmm",
        )
        source_id = source.id
        service.start(source.id, stage="building", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": crawl_id,
                "knowledge_path": f"data/runs/pipeline/{crawl_id}/processed/canonical/knowledge.json",
                "manifest_path": f"data/runs/pipeline/{crawl_id}/processed/canonical/manifest.json",
                "build_report_path": f"data/runs/pipeline/{crawl_id}/processed/canonical/build_report.json",
                "knowledge_version": "canonical-staging-test",
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_import"
    assert body["scope"] == "screen"
    assert body["target"] == "/admin/cuentasxcobrar/retenciones"
    assert body["parameters"]["activation_mode"] == "staging_only"
    assert body["parameters"]["source_canonical_job_id"] == str(source_id)
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_import_rejects_wrong_or_unfinished_source(api):
    client, factory, dispatcher = api
    with factory.begin() as session:
        wrong = PipelineJobService(session).create(kind="crawl", scope="full")
        wrong_id = wrong.id
        pending = PipelineJobService(session).create(kind="canonical_build", scope="full")
        pending_id = pending.id

    assert client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(wrong_id)},
    ).status_code == 409
    assert client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(pending_id)},
    ).status_code == 409
    assert dispatcher.submitted == []



def seed_active_version(factory, *, status=KnowledgeVersionStatus.ACTIVE):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:sync-test",
            slug="sync-test",
            name="ERP Sync Test",
            profile_name="test",
            safe_metadata={},
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="knowledge.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="active-v1",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.0",
            knowledge_version="active-v1",
            canonical_hash="a" * 64,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=status,
        )
        session.add(version)
        session.flush()
        return str(version.id), erp.id


def test_projection_sync_jobs_capture_only_the_single_active_version(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_version(factory)

    neo4j = client.post(
        "/api/admin/pipeline-jobs/neo4j-sync",
        json={"batch_size": 150, "replace_version": False},
    )
    assert neo4j.status_code == 202, neo4j.text
    neo = neo4j.json()
    assert neo["kind"] == "neo4j_sync"
    assert neo["scope"] == "version"
    assert neo["target"] == "active-v1"
    assert neo["erp_id"] == erp_id
    assert neo["knowledge_version_id"] == version_id
    assert neo["parameters"]["active_only"] is True
    assert neo["parameters"]["knowledge_version_id"] == version_id
    assert neo["parameters"]["batch_size"] == 150

    chroma = client.post("/api/admin/pipeline-jobs/chroma-sync", json={})
    assert chroma.status_code == 202, chroma.text
    vec = chroma.json()
    assert vec["kind"] == "chroma_sync"
    assert vec["scope"] == "version"
    assert vec["target"] == "active-v1"
    assert vec["parameters"]["active_only"] is True
    assert vec["parameters"]["knowledge_version_id"] == version_id
    assert len(dispatcher.submitted) == 2


def test_projection_sync_rejects_when_there_is_no_active_version(api):
    client, factory, dispatcher = api
    seed_active_version(factory, status=KnowledgeVersionStatus.IMPORTED)
    assert client.post("/api/admin/pipeline-jobs/neo4j-sync", json={}).status_code == 409
    assert client.post("/api/admin/pipeline-jobs/chroma-sync", json={}).status_code == 409
    assert dispatcher.submitted == []
