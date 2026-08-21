from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import ERPSystemRecord, ImportRun, KnowledgeItem, KnowledgeVersionRecord, ReviewAction
from src.knowledge.canonical.enums import ReviewStatus

HASH = "a" * 64


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 50000)),
                base_url="http://test",
            ) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture
def api(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    settings = replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'review.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, semantic_review_session_factory=factory)
    yield Client(app), factory
    engine.dispose()


def seed(factory, *, version_status=KnowledgeVersionStatus.IMPORTED):
    with factory.begin() as session:
        erp = ERPSystemRecord(id="erp:test", slug="test", name="Test ERP", profile_name="test", safe_metadata={})
        run = ImportRun(
            erp=erp,
            source_knowledge_path="knowledge.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="staging-v1",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.1.0",
            knowledge_version="staging-v1",
            canonical_hash=HASH,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=version_status,
        )
        payload = {
            "id": "screen:test",
            "erp_id": erp.id,
            "module_id": None,
            "title": "Pantalla de prueba",
            "normalized_title": "pantalla de prueba",
            "route": "/test",
            "main_content_text": "",
            "description": None,
            "source_refs": [],
            "evidence_ids": [],
            "metadata": {},
        }
        item = KnowledgeItem(
            knowledge_version=version,
            canonical_id="screen:test",
            entity_type="screen",
            title="Pantalla de prueba",
            normalized_title="pantalla de prueba",
            route="/test",
            content_hash=HASH,
            source_payload=payload,
            generated_review_status=ReviewStatus.PENDING_REVIEW,
            current_review_status=ReviewStatus.PENDING_REVIEW,
        )
        session.add(item)
        session.flush()
        return str(version.id), str(item.id)


def body(**changes):
    value = {
        "reviewer_id": "reviewer:local",
        "reason": "Revisión manual de prueba.",
        "expected_status": "pending_review",
        "expected_revision": 0,
    }
    value.update(changes)
    return value


def test_list_and_detail_include_staging_pending_items(api):
    client, factory = api
    version_id, item_id = seed(factory)
    response = client.get(f"/api/admin/structural-review/items?knowledge_version_id={version_id}&status=pending_review")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 1
    assert data["status_counts"]["pending_review"] == 1
    assert data["items"][0]["id"] == item_id
    detail = client.get(f"/api/admin/structural-review/items/{item_id}")
    assert detail.status_code == 200
    assert detail.json()["effective_payload"]["id"] == "screen:test"


def test_approve_uses_api_source_and_concurrency_contract(api):
    client, factory = api
    _version_id, item_id = seed(factory)
    response = client.post(f"/api/admin/structural-review/items/{item_id}/approve", json=body())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["performed_action"] == "approve"
    assert data["current_review_status"] == "approved"
    assert data["review_revision"] == 1
    with factory() as session:
        action = session.scalar(select(ReviewAction))
        assert str(action.source) == "api"
    stale = client.post(f"/api/admin/structural-review/items/{item_id}/approve", json=body())
    assert stale.status_code == 409


def test_reject_requires_reason_and_reset_returns_to_pending(api):
    client, factory = api
    _version_id, item_id = seed(factory)
    missing_reason = client.post(
        f"/api/admin/structural-review/items/{item_id}/reject",
        json=body(reason=None),
    )
    assert missing_reason.status_code == 422
    approved = client.post(f"/api/admin/structural-review/items/{item_id}/approve", json=body())
    assert approved.status_code == 200
    rejected = client.post(
        f"/api/admin/structural-review/items/{item_id}/reject",
        json=body(expected_status="approved", expected_revision=1, reason="No corresponde."),
    )
    assert rejected.status_code == 200
    reset = client.post(
        f"/api/admin/structural-review/items/{item_id}/reset",
        json=body(expected_status="rejected", expected_revision=2),
    )
    assert reset.status_code == 200
    assert reset.json()["current_review_status"] == "pending_review"


def test_correction_preserves_identity_and_updates_effective_payload(api):
    client, factory = api
    _version_id, item_id = seed(factory)
    detail = client.get(f"/api/admin/structural-review/items/{item_id}").json()
    corrected = dict(detail["source_payload"])
    corrected["description"] = "Descripción corregida por revisión humana"
    response = client.post(
        f"/api/admin/structural-review/items/{item_id}/correct",
        json={**body(), "corrected_payload": corrected},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["current_review_status"] == "corrected"
    assert data["was_corrected"] is True
    assert data["effective_payload"]["description"] == "Descripción corregida por revisión humana"
    assert data["source_payload"]["description"] is None


def test_archived_version_is_readable_but_not_mutable(api):
    client, factory = api
    version_id, item_id = seed(factory, version_status=KnowledgeVersionStatus.ARCHIVED)
    assert client.get(f"/api/admin/structural-review/items?knowledge_version_id={version_id}").status_code == 200
    response = client.post(f"/api/admin/structural-review/items/{item_id}/approve", json=body())
    assert response.status_code == 409
