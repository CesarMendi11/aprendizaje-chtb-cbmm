from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus
from src.database.models import KnowledgeItem, KnowledgeVersionRecord, PipelineJob, SyncJob
from src.database.services import CanonicalImportService, KnowledgeReviewService
from tests.canonical_fixtures import exported_fictional_canonical


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
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'promotion.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, semantic_review_session_factory=factory)
    yield Client(app), factory, tmp_path
    engine.dispose()


def seed(factory, tmp_path):
    canonical_dir = exported_fictional_canonical(tmp_path / "canonical")
    with factory.begin() as session:
        imported = CanonicalImportService(session).import_canonical(
            canonical_dir / "knowledge.json",
            canonical_dir / "manifest.json",
            canonical_dir / "build_report.json",
            activate=False,
            create_sync_jobs=False,
        )
        version = session.get(KnowledgeVersionRecord, uuid.UUID(imported.version_id))
        source = PipelineJob(
            kind=PipelineJobKind.CANONICAL_BUILD,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.FULL,
            target=None,
            profile_name="synthetic",
            request_source="admin_api",
            parameters={},
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={},
            result_payload={
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "knowledge_version": version.knowledge_version,
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
        import_job = PipelineJob(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.FULL,
            target=None,
            profile_name="synthetic",
            erp_id=version.erp_id,
            knowledge_version_id=version.id,
            request_source="admin_api",
            parameters={
                "source_canonical_job_id": str(source.id),
                "activation_mode": "staging_only",
            },
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={},
            result_payload={
                "staging_ready": True,
                "activation_performed": False,
                "knowledge_version": version.knowledge_version,
                "knowledge_version_id": str(version.id),
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(import_job)
        version_id = version.id
        knowledge_version = version.knowledge_version
    return version_id, knowledge_version


def approve_required(factory, version_id):
    with factory.begin() as session:
        items = list(
            session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == version_id,
                    KnowledgeItem.entity_type.in_(("erp_system", "module")),
                )
            )
        )
        service = KnowledgeReviewService(session)
        for item in items:
            service.approve(item.id, reviewer="reviewer:api")


def test_assessment_and_bootstrap_promotion(api):
    client, factory, tmp_path = api
    version_id, knowledge_version = seed(factory, tmp_path)

    pending = client.get(
        f"/api/admin/knowledge-versions/{version_id}/promotion-assessment"
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["promotable"] is False
    assert any(
        blocker["code"] == "required_pending_review"
        for blocker in pending.json()["blockers"]
    )

    approve_required(factory, version_id)

    ready = client.get(
        f"/api/admin/knowledge-versions/{version_id}/promotion-assessment"
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["promotable"] is True

    promoted = client.post(
        f"/api/admin/knowledge-versions/{version_id}/promote",
        json={
            "reviewer_id": "reviewer:api",
            "reason": "Primera versión FULL vNext revisada para bootstrap.",
            "expected_knowledge_version": knowledge_version,
            "confirm_promotion": True,
        },
    )
    assert promoted.status_code == 200, promoted.text
    body = promoted.json()
    assert body["knowledge_version_id"] == str(version_id)
    assert set(body["sync_jobs"]) == {"neo4j", "chromadb"}

    with factory() as session:
        version = session.get(KnowledgeVersionRecord, version_id)
        assert str(version.status) == "active"
        assert len(list(session.scalars(select(SyncJob)))) == 2


def test_api_replacement_promotion_archives_previous_active(api):
    from tests.test_version_diff_service import seed_reconciled

    client, factory, tmp_path = api
    with factory() as session:
        active_id, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)

    assessment = client.get(
        f"/api/admin/knowledge-versions/{candidate_id}/promotion-assessment"
    )
    assert assessment.status_code == 200, assessment.text
    body = assessment.json()
    assert body["promotion_mode"] == "replacement"
    assert body["promotable"] is True

    promoted = client.post(
        f"/api/admin/knowledge-versions/{candidate_id}/promote",
        json={
            "reviewer_id": "reviewer:api",
            "reason": "Reemplazo reconciliado revisado.",
            "expected_knowledge_version": body["knowledge_version"],
            "confirm_promotion": True,
        },
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["previous_active_version_id"] == str(active_id)

    with factory() as session:
        active = session.get(KnowledgeVersionRecord, active_id)
        candidate = session.get(KnowledgeVersionRecord, candidate_id)
        assert str(active.status) == "archived"
        assert str(candidate.status) == "active"
