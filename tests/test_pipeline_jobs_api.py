from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    SyncStatus,
    SyncTarget,
)
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SyncJob,
)
from src.database.services import PipelineJobService
from src.knowledge.canonical.enums import ReviewStatus
from tests.crawl_quality_fixtures import certified_crawl_quality, source_crawl_result
from tests.removal_review_fixtures import resolve_all_removals
from tests.test_removal_reconciliation_plan_service import partial_candidate
from tests.test_version_diff_service import seed as seed_version_diff


class Client:
    def __init__(self, app):
        self.app = app

    def get(self, path):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 50000)),
                base_url="http://test",
            ) as client:
                return await client.get(path)

        return asyncio.run(send())

    def post(self, path, json):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 50000)),
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
    settings = replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True)
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
    version_id, erp_id, screen_id = seed_active_crawl_screen(factory)
    response = client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={
            "scope": "screen",
            "target": "/admin/cuentasxcobrar/retenciones",
            "knowledge_version_id": version_id,
            "headless": False,
            "slow_mo": 120,
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "crawl"
    assert body["scope"] == "screen"
    assert body["status"] == "queued"
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"] == {
        "headless": False,
        "slow_mo": 120,
        "active_only": True,
        "target_screen_id": screen_id,
        "knowledge_version_id": version_id,
        "knowledge_version": "active-v1",
        "erp_id": erp_id,
    }
    assert [str(value) for value in dispatcher.submitted] == [body["id"]]

    with factory() as session:
        stored = PipelineJobService(session).jobs.get(body["id"])
        assert stored is not None
        assert stored.target == "/admin/cuentasxcobrar/retenciones"


def test_create_module_crawl_pins_target_to_active_knowledge_version(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_module(factory)

    response = client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={
            "scope": "module",
            "target_module_id": "module:tracking",
            "knowledge_version_id": version_id,
            "headless": True,
            "slow_mo": 50,
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["scope"] == "module"
    assert body["target"] == "module:tracking"
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"] == {
        "headless": True,
        "slow_mo": 50,
        "active_only": True,
        "target_module_id": "module:tracking",
        "knowledge_version_id": version_id,
        "knowledge_version": "active-v1",
        "erp_id": erp_id,
    }
    assert [str(value) for value in dispatcher.submitted] == [body["id"]]

    with factory() as session:
        stored = PipelineJobService(session).jobs.get(body["id"])
        assert stored is not None
        assert stored.scope.value == "module"
        assert stored.target == "module:tracking"
        assert str(stored.knowledge_version_id) == version_id
        assert stored.erp_id == erp_id


def test_module_crawl_requires_canonical_module_id_and_rejects_route_target(api):
    client, _, dispatcher = api

    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={"scope": "module"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={"scope": "module", "target_module_id": "tracking"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={
                "scope": "module",
                "target": "/admin/tracking",
                "target_module_id": "module:tracking",
            },
        ).status_code
        == 422
    )
    assert dispatcher.submitted == []


def test_partial_crawl_rejects_target_when_pinned_active_version_does_not_match(api):
    client, factory, dispatcher = api
    version_id, _erp_id, _screen_id = seed_active_crawl_screen(factory)
    wrong_version_id = str(uuid.uuid4())

    response = client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={
            "scope": "screen",
            "target": "/admin/cuentasxcobrar/retenciones",
            "knowledge_version_id": wrong_version_id,
        },
    )

    assert response.status_code == 409
    assert "versión ACTIVE indicada" in response.json()["detail"]
    assert wrong_version_id != version_id
    assert dispatcher.submitted == []


def test_module_crawl_rejects_target_not_present_in_active_knowledge(api):
    client, _, dispatcher = api

    response = client.post(
        "/api/admin/pipeline-jobs/crawl",
        json={"scope": "module", "target_module_id": "module:missing"},
    )

    assert response.status_code == 409
    assert "ACTIVE" in response.json()["detail"]
    assert dispatcher.submitted == []


def test_create_full_crawl_rejects_target_and_screen_requires_internal_route(api):
    client, _, dispatcher = api
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={"scope": "full", "target": "/admin/retenciones"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={"scope": "screen", "target": "https://example.test/admin/x"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={
                "scope": "screen",
                "target": "/admin/x",
                "target_module_id": "module:x",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={"scope": "full", "target_module_id": "module:x"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/crawl",
            json={"scope": "full", "knowledge_version_id": str(uuid.uuid4())},
        ).status_code
        == 422
    )
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
    version_id, erp_id, screen_id = seed_active_crawl_screen(factory)
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="crawl",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            profile_name="cbmm",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
            parameters={
                "target_screen_id": screen_id,
                "knowledge_version_id": version_id,
                "knowledge_version": "active-v1",
                "erp_id": erp_id,
            },
        )
        source_id = source.id
        service.start(source.id, stage="running")
        service.succeed(
            source.id,
            result_payload={
                "artifact_root": f"data/runs/pipeline/{source.id}",
                **source_crawl_result(
                    source.id,
                    scope="screen",
                    target="/admin/cuentasxcobrar/retenciones",
                ),
            },
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
    assert body["parameters"] == {
        "source_crawl_job_id": str(source_id),
        "source_crawl_result": source_crawl_result(
            source_id,
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
        ),
        "base_knowledge_version_id": version_id,
        "base_knowledge_version": "active-v1",
        "erp_id": erp_id,
        "target_screen_id": screen_id,
    }
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
            scope="full",
            target=None,
            profile_name="cbmm",
        )
        source_id = source.id
        service.start(source.id, stage="building", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": crawl_id,
                "knowledge_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/canonical/knowledge.json"
                ),
                "manifest_path": f"data/runs/pipeline/{crawl_id}/processed/canonical/manifest.json",
                "build_report_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/canonical/build_report.json"
                ),
                "knowledge_version": "canonical-staging-test",
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="full", target=None
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_import"
    assert body["scope"] == "full"
    assert body["target"] is None
    assert body["parameters"]["activation_mode"] == "staging_only"
    assert body["parameters"]["source_canonical_job_id"] == str(source_id)
    assert body["parameters"]["expected_crawl_execution_quality"] == (
        certified_crawl_quality(run_id=crawl_id, scope="full", target=None)
    )
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_import_rejects_wrong_or_unfinished_source(api):
    client, factory, dispatcher = api
    with factory.begin() as session:
        wrong = PipelineJobService(session).create(kind="crawl", scope="full")
        wrong_id = wrong.id
        pending = PipelineJobService(session).create(kind="canonical_build", scope="full")
        pending_id = pending.id

    assert (
        client.post(
            "/api/admin/pipeline-jobs/canonical-import",
            json={"source_canonical_job_id": str(wrong_id)},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/admin/pipeline-jobs/canonical-import",
            json={"source_canonical_job_id": str(pending_id)},
        ).status_code
        == 409
    )
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


def seed_active_crawl_screen(
    factory,
    *,
    route="/admin/cuentasxcobrar/retenciones",
    status=KnowledgeVersionStatus.ACTIVE,
):
    version_id, erp_id = seed_active_version(factory, status=status)
    with factory.begin() as session:
        screen = KnowledgeItem(
            knowledge_version_id=uuid.UUID(version_id),
            canonical_id="screen:retenciones",
            entity_type="screen",
            parent_canonical_id=None,
            title="Retenciones",
            normalized_title="retenciones",
            route=route,
            content_hash="b" * 64,
            source_payload={
                "id": "screen:retenciones",
                "erp_id": erp_id,
                "route": route,
                "title": "Retenciones",
            },
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        session.add(screen)
    return version_id, erp_id, "screen:retenciones"


def test_create_canonical_build_preserves_module_base_provenance(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_version(factory)
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="crawl",
            scope="module",
            target="module:tracking",
            profile_name="cbmm",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
            parameters={
                "target_module_id": "module:tracking",
                "knowledge_version_id": version_id,
                "knowledge_version": "active-v1",
                "erp_id": erp_id,
            },
        )
        source_id = source.id
        service.start(source.id, stage="running")
        service.succeed(
            source.id,
            result_payload={
                "artifact_root": f"data/runs/pipeline/{source.id}",
                **source_crawl_result(
                    source.id, scope="module", target="module:tracking"
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-build",
        json={"source_crawl_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["scope"] == "module"
    assert body["target"] == "module:tracking"
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"] == {
        "source_crawl_job_id": str(source_id),
        "source_crawl_result": source_crawl_result(
            source_id, scope="module", target="module:tracking"
        ),
        "target_module_id": "module:tracking",
        "base_knowledge_version_id": version_id,
        "base_knowledge_version": "active-v1",
        "erp_id": erp_id,
    }
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_import_rejects_partial_snapshot(api):
    client, factory, dispatcher = api
    crawl_id = "00000000-0000-0000-0000-000000000457"
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="module",
            target="module:tracking",
            profile_name="cbmm",
        )
        source_id = source.id
        service.start(source.id, stage="building", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": crawl_id,
                "knowledge_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/canonical/knowledge.json"
                ),
                "manifest_path": f"data/runs/pipeline/{crawl_id}/processed/canonical/manifest.json",
                "build_report_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/canonical/build_report.json"
                ),
                "knowledge_version": "partial-module-test",
                "snapshot_mode": "partial",
                "snapshot_scope": "module",
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="module", target="module:tracking"
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 409
    assert "canonical parcial" in response.json()["detail"]
    assert dispatcher.submitted == []


def seed_active_module(factory):
    version_id, erp_id = seed_active_version(factory)
    with factory.begin() as session:
        module = KnowledgeItem(
            knowledge_version_id=uuid.UUID(version_id),
            canonical_id="module:tracking",
            entity_type="module",
            parent_canonical_id=erp_id,
            title="Tracking",
            normalized_title="tracking",
            route=None,
            content_hash="c" * 64,
            source_payload={
                "id": "module:tracking",
                "name": "Tracking",
                "depth": 0,
                "navigation_path": ["Tracking"],
                "metadata": {"navigation_origin_path": "#tracking"},
            },
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        session.add(module)
        session.flush()
    return version_id, erp_id


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

    semantic = client.post("/api/admin/pipeline-jobs/semantic-sync", json={})
    assert semantic.status_code == 202, semantic.text
    sem = semantic.json()
    assert sem["kind"] == "semantic_sync"
    assert sem["scope"] == "version"
    assert sem["target"] == "active-v1"
    assert sem["parameters"]["active_only"] is True
    assert sem["parameters"]["knowledge_version_id"] == version_id
    assert sem["parameters"]["projection"] == "semantic_chromadb"
    assert len(dispatcher.submitted) == 3

    duplicate = client.post("/api/admin/pipeline-jobs/semantic-sync", json={})
    assert duplicate.status_code == 409
    assert "Ya existe un job semantic_sync" in duplicate.json()["detail"]
    assert len(dispatcher.submitted) == 3


def test_failed_structural_sync_is_queued_as_a_retry_with_lineage(api):
    client, factory, dispatcher = api
    version_id, _erp_id = seed_active_version(factory)
    with factory.begin() as session:
        sync_job = SyncJob(
            knowledge_version_id=uuid.UUID(version_id),
            target=SyncTarget.NEO4J,
            status=SyncStatus.FAILED,
            attempt_count=2,
            error_summary="Synthetic projection failure",
        )
        session.add(sync_job)
        session.flush()
        sync_job_id = str(sync_job.id)

    response = client.post(
        "/api/admin/pipeline-jobs/neo4j-sync",
        json={"batch_size": 200, "replace_version": False},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["request_source"] == "admin_api_retry"
    assert payload["parameters"]["sync_job_id"] == sync_job_id
    assert payload["parameters"]["sync_job_status_at_queue"] == "failed"
    assert payload["parameters"]["sync_job_attempt_count_at_queue"] == 2
    assert len(dispatcher.submitted) == 1


def test_projection_sync_rejects_when_there_is_no_active_version(api):
    client, factory, dispatcher = api
    seed_active_version(factory, status=KnowledgeVersionStatus.IMPORTED)
    assert client.post("/api/admin/pipeline-jobs/neo4j-sync", json={}).status_code == 409
    assert client.post("/api/admin/pipeline-jobs/chroma-sync", json={}).status_code == 409
    assert client.post("/api/admin/pipeline-jobs/semantic-sync", json={}).status_code == 409
    assert dispatcher.submitted == []


def seed_active_screen(factory, *, review=ReviewStatus.APPROVED):
    version_id, erp_id = seed_active_version(factory)
    with factory.begin() as session:
        screen = KnowledgeItem(
            knowledge_version_id=uuid.UUID(version_id),
            canonical_id="screen:retenciones-active",
            entity_type="screen",
            title="Retenciones",
            normalized_title="retenciones",
            route="/admin/cuentasxcobrar/retenciones",
            content_hash="b" * 64,
            source_payload={"id": "screen:retenciones-active", "title": "Retenciones"},
            generated_review_status=review,
            current_review_status=review,
        )
        session.add(screen)
        session.flush()
        return version_id, erp_id, str(screen.id), screen.canonical_id


def test_semantic_inference_job_captures_only_reviewed_screen_from_active_version(api):
    client, factory, dispatcher = api
    version_id, erp_id, screen_item_id, screen_id = seed_active_screen(factory)

    response = client.post(
        "/api/admin/pipeline-jobs/semantic-inference",
        json={"screen_id": screen_id},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "semantic_inference"
    assert body["scope"] == "screen"
    assert body["target"] == "/admin/cuentasxcobrar/retenciones"
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"]["active_only"] is True
    assert body["parameters"]["semantic_type"] == "screen_purpose"
    assert body["parameters"]["screen_knowledge_item_id"] == screen_item_id
    assert body["parameters"]["screen_id"] == screen_id
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_semantic_inference_job_rejects_unreviewed_or_unknown_screen(api):
    client, factory, dispatcher = api
    _version_id, _erp_id, _screen_item_id, screen_id = seed_active_screen(
        factory, review=ReviewStatus.PENDING_REVIEW
    )

    pending = client.post(
        "/api/admin/pipeline-jobs/semantic-inference",
        json={"screen_id": screen_id},
    )
    assert pending.status_code == 409
    unknown = client.post(
        "/api/admin/pipeline-jobs/semantic-inference",
        json={"screen_id": "screen:unknown"},
    )
    assert unknown.status_code == 404
    invalid = client.post(
        "/api/admin/pipeline-jobs/semantic-inference",
        json={"screen_id": "not-a-screen"},
    )
    assert invalid.status_code == 422
    assert dispatcher.submitted == []


def test_create_canonical_merge_pins_partial_to_exact_active_base(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_version(factory)
    crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="module",
            target="module:tracking",
            profile_name="cbmm",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
            parameters={"source_crawl_job_id": str(crawl_id)},
        )
        source_id = source.id
        service.start(source.id, stage="building", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/canonical/knowledge.json"
                ),
                "manifest_path": f"data/runs/pipeline/{crawl_id}/processed/canonical/manifest.json",
                "build_report_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/canonical/build_report.json"
                ),
                "knowledge_version": "partial-v1",
                "snapshot_mode": "partial",
                "snapshot_scope": "module",
                "target_module_id": "module:tracking",
                "base_knowledge_version_id": version_id,
                "base_knowledge_version": "active-v1",
                "erp_id": erp_id,
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="module", target="module:tracking"
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-merge",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_merge"
    assert body["scope"] == "module"
    assert body["target"] == "module:tracking"
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"]["base_knowledge_version_id"] == version_id
    assert body["parameters"]["base_knowledge_version"] == "active-v1"
    assert body["parameters"]["expected_partial_knowledge_version"] == "partial-v1"
    assert body["parameters"]["expected_crawl_execution_quality"] == (
        certified_crawl_quality(
            run_id=crawl_id, scope="module", target="module:tracking"
        )
    )
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_merge_rejects_when_pinned_base_is_no_longer_active(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_version(factory)
    crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="module",
            target="module:tracking",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
        )
        source_id = source.id
        service.start(source.id, stage="building")
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "knowledge.json",
                "manifest_path": "manifest.json",
                "build_report_path": "build_report.json",
                "knowledge_version": "partial-v1",
                "snapshot_mode": "partial",
                "snapshot_scope": "module",
                "target_module_id": "module:tracking",
                "base_knowledge_version_id": version_id,
                "base_knowledge_version": "active-v1",
                "erp_id": erp_id,
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="module", target="module:tracking"
                ),
            },
        )
        session.get(
            KnowledgeVersionRecord, uuid.UUID(version_id)
        ).status = KnowledgeVersionStatus.ARCHIVED

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-merge",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 409
    assert "ACTIVE" in response.json()["detail"]
    assert dispatcher.submitted == []


def test_create_canonical_import_accepts_full_candidate_from_merge_and_repins_base(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_version(factory)
    crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_merge",
            scope="module",
            target="module:tracking",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
        )
        source_id = source.id
        service.start(source.id, stage="merging", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/"
                    f"canonical-merged/{source.id}/knowledge.json"
                ),
                "manifest_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/"
                    f"canonical-merged/{source.id}/manifest.json"
                ),
                "build_report_path": (
                    f"data/runs/pipeline/{crawl_id}/processed/"
                    f"canonical-merged/{source.id}/build_report.json"
                ),
                "knowledge_version": "merged-v2",
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "merged_from_scope": "module",
                "target_module_id": "module:tracking",
                "base_knowledge_version_id": version_id,
                "base_knowledge_version": "active-v1",
                "erp_id": erp_id,
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="module", target="module:tracking"
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_import"
    assert body["scope"] == "full"
    assert body["target"] is None
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"]["requires_active_base"] is True
    assert body["parameters"]["base_knowledge_version_id"] == version_id
    assert body["parameters"]["base_knowledge_version"] == "active-v1"
    assert body["parameters"]["merged_from_scope"] == "module"
    assert body["parameters"]["merged_target_module_id"] == "module:tracking"
    assert body["parameters"]["expected_crawl_execution_quality"] == (
        certified_crawl_quality(
            run_id=crawl_id, scope="module", target="module:tracking"
        )
    )
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_reconciliation_queues_resolved_removal_hitl(api, tmp_path):
    client, factory, dispatcher = api
    with factory() as session:
        _active_id, candidate_id = partial_candidate(session, tmp_path)
        resolve_all_removals(session, candidate_id)
        session.commit()
        candidate_id_text = str(candidate_id)

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-reconciliation",
        json={"candidate_version_id": candidate_id_text},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_reconciliation"
    assert body["scope"] == "version"
    assert body["target"] is None
    assert body["knowledge_version_id"] == candidate_id_text
    assert body["parameters"]["candidate_version_id"] == candidate_id_text
    assert body["parameters"]["candidate_knowledge_version"]
    assert body["parameters"]["active_version_id"]
    assert body["parameters"]["active_knowledge_version"]
    assert body["parameters"]["erp_id"]
    assert str(dispatcher.submitted[-1]) == body["id"]



def test_create_canonical_reconciliation_accepts_resolved_full_candidate(api, tmp_path):
    client, factory, dispatcher = api
    with factory() as session:
        _active_id, candidate_id, _ = seed_version_diff(session, tmp_path)
        review = resolve_all_removals(session, candidate_id)
        assert review.candidate_origin == "full_canonical"
        session.commit()
        candidate_id_text = str(candidate_id)

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-reconciliation",
        json={"candidate_version_id": candidate_id_text},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_reconciliation"
    assert body["scope"] == "version"
    assert body["knowledge_version_id"] == candidate_id_text
    assert body["parameters"]["candidate_version_id"] == candidate_id_text
    assert str(dispatcher.submitted[-1]) == body["id"]

def test_create_canonical_import_accepts_hitl_reconciliation_source(api):
    client, factory, dispatcher = api
    active_id, erp_id = seed_active_version(factory)
    with factory.begin() as session:
        active = session.get(KnowledgeVersionRecord, uuid.UUID(active_id))
        raw_run = ImportRun(
            erp=active.erp,
            source_knowledge_path="raw-knowledge.json",
            source_manifest_path="raw-manifest.json",
            requested_knowledge_version="raw-v2",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        raw = KnowledgeVersionRecord(
            erp=active.erp,
            import_run=raw_run,
            schema_version="1.0",
            knowledge_version="raw-v2",
            canonical_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.IMPORTED,
        )
        session.add(raw)
        session.flush()
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_reconciliation",
            scope="version",
            erp_id=erp_id,
            knowledge_version_id=raw.id,
            parameters={
                "candidate_version_id": str(raw.id),
                "candidate_knowledge_version": raw.knowledge_version,
                "active_version_id": active_id,
                "active_knowledge_version": "active-v1",
                "erp_id": erp_id,
            },
        )
        source_id = source.id
        service.start(source.id, stage="reconciling", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "erp_id": erp_id,
                "raw_candidate_version_id": str(raw.id),
                "raw_candidate_knowledge_version": raw.knowledge_version,
                "base_active_version_id": active_id,
                "base_active_knowledge_version": "active-v1",
                "reconciled_knowledge_version": "reconciled-v3",
                "decision_set_hash": "c" * 64,
                "unresolved_total": 0,
                "decisions": [
                    {
                        "decision": "retain_from_active",
                        "requires_human_review": False,
                        "review_set_id": str(uuid.uuid4()),
                        "review_decision_id": str(uuid.uuid4()),
                        "review_action_id": str(uuid.uuid4()),
                        "review_revision": 1,
                    }
                ],
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_reconciliation_job_id": str(source_id)},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["kind"] == "canonical_import"
    assert body["scope"] == "version"
    assert body["erp_id"] == erp_id
    assert body["parameters"]["source_reconciliation_job_id"] == str(source_id)
    assert body["parameters"]["expected_knowledge_version"] == "reconciled-v3"
    assert body["parameters"]["expected_decision_set_hash"] == "c" * 64
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_merge_accepts_screen_partial_with_exact_active_pin(api):
    client, factory, dispatcher = api
    version_id, erp_id, screen_id = seed_active_crawl_screen(factory)
    crawl_id = uuid.uuid4()
    route = "/admin/cuentasxcobrar/retenciones"
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="screen",
            target=route,
            profile_name="cbmm",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
        )
        source_id = source.id
        service.start(source.id, stage="building", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "knowledge.json",
                "manifest_path": "manifest.json",
                "build_report_path": "build_report.json",
                "knowledge_version": "partial-screen-v1",
                "snapshot_mode": "partial",
                "snapshot_scope": "screen",
                "snapshot_target": route,
                "target_screen_id": screen_id,
                "base_knowledge_version_id": version_id,
                "base_knowledge_version": "active-v1",
                "erp_id": erp_id,
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="screen", target=route
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-merge",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["scope"] == "screen"
    assert body["target"] == route
    assert body["erp_id"] == erp_id
    assert body["knowledge_version_id"] == version_id
    assert body["parameters"]["target_screen_id"] == screen_id
    assert body["parameters"]["base_knowledge_version_id"] == version_id
    assert body["parameters"]["base_knowledge_version"] == "active-v1"
    assert body["parameters"]["expected_crawl_execution_quality"] == (
        certified_crawl_quality(run_id=crawl_id, scope="screen", target=route)
    )
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_import_accepts_full_screen_merge_and_repins_base(api):
    client, factory, dispatcher = api
    version_id, erp_id, screen_id = seed_active_crawl_screen(factory)
    crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_merge",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
        )
        source_id = source.id
        service.start(source.id, stage="merging", progress_total=4)
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "knowledge.json",
                "manifest_path": "manifest.json",
                "build_report_path": "build_report.json",
                "knowledge_version": "merged-screen-v2",
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "merged_from_scope": "screen",
                "target_screen_id": screen_id,
                "base_knowledge_version_id": version_id,
                "base_knowledge_version": "active-v1",
                "erp_id": erp_id,
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id,
                    scope="screen",
                    target="/admin/cuentasxcobrar/retenciones",
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["scope"] == "full"
    assert body["parameters"]["requires_active_base"] is True
    assert body["parameters"]["merged_from_scope"] == "screen"
    assert body["parameters"]["merged_target_screen_id"] == screen_id
    assert body["parameters"]["base_knowledge_version_id"] == version_id
    assert body["parameters"]["expected_crawl_execution_quality"] == (
        certified_crawl_quality(
            run_id=crawl_id,
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
        )
    )
    assert str(dispatcher.submitted[-1]) == body["id"]


def test_create_canonical_build_rejects_legacy_crawl_without_quality_pins(api):
    client, factory, dispatcher = api
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(kind="crawl", scope="full", profile_name="cbmm")
        source_id = source.id
        service.start(source.id, stage="running")
        service.succeed(
            source.id,
            result_payload={
                "artifact_root": f"data/runs/pipeline/{source.id}",
                "run_id": str(source.id),
                "scope": "full",
                "target": None,
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-build",
        json={"source_crawl_job_id": str(source_id)},
    )

    assert response.status_code == 409
    assert "resumen requerido" in response.json()["detail"]
    assert dispatcher.submitted == []


def test_create_canonical_import_rejects_legacy_canonical_without_quality_contract(api):
    client, factory, dispatcher = api
    crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="full",
            target=None,
            profile_name="cbmm",
        )
        source_id = source.id
        service.start(source.id, stage="building")
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "knowledge.json",
                "manifest_path": "manifest.json",
                "build_report_path": "build_report.json",
                "knowledge_version": "legacy-full",
                "snapshot_mode": "full",
                "snapshot_scope": "full",
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )

    assert response.status_code == 409
    assert "contrato versionado" in response.json()["detail"]
    assert dispatcher.submitted == []


def test_create_canonical_merge_rejects_legacy_partial_without_quality_contract(api):
    client, factory, dispatcher = api
    version_id, erp_id = seed_active_version(factory)
    crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="module",
            target="module:tracking",
            profile_name="cbmm",
            erp_id=erp_id,
            knowledge_version_id=uuid.UUID(version_id),
        )
        source_id = source.id
        service.start(source.id, stage="building")
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "knowledge.json",
                "manifest_path": "manifest.json",
                "build_report_path": "build_report.json",
                "knowledge_version": "legacy-partial",
                "snapshot_mode": "partial",
                "snapshot_scope": "module",
                "target_module_id": "module:tracking",
                "base_knowledge_version_id": version_id,
                "base_knowledge_version": "active-v1",
                "erp_id": erp_id,
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-merge",
        json={"source_canonical_job_id": str(source_id)},
    )

    assert response.status_code == 409
    assert "contrato versionado" in response.json()["detail"]
    assert dispatcher.submitted == []


def test_create_canonical_import_rejects_quality_bound_to_different_crawl(api):
    client, factory, dispatcher = api
    crawl_id = uuid.uuid4()
    other_crawl_id = uuid.uuid4()
    with factory.begin() as session:
        service = PipelineJobService(session)
        source = service.create(
            kind="canonical_build",
            scope="full",
            target=None,
            profile_name="cbmm",
        )
        source_id = source.id
        service.start(source.id, stage="building")
        service.succeed(
            source.id,
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "knowledge.json",
                "manifest_path": "manifest.json",
                "build_report_path": "build_report.json",
                "knowledge_version": "mismatched-quality",
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "crawl_execution_quality": certified_crawl_quality(
                    run_id=other_crawl_id, scope="full", target=None
                ),
            },
        )

    response = client.post(
        "/api/admin/pipeline-jobs/canonical-import",
        json={"source_canonical_job_id": str(source_id)},
    )

    assert response.status_code == 409
    assert "source_crawl_job_id" in response.json()["detail"]
    assert dispatcher.submitted == []
