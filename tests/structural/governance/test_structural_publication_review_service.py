from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from erp_assistant.api.app import create_app
from erp_assistant.config.api_settings import ApiSettings
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    ReviewSource,
    SyncStatus,
    SyncTarget,
)
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    ReviewAction,
    SyncJob,
)
from erp_assistant.structural.services.structural_publication_review_service import (
    StructuralPublicationReviewConflictError,
    StructuralPublicationReviewError,
    StructuralPublicationReviewService,
)
from erp_assistant.structural.canonical.enums import ReviewStatus

HASH = "a" * 64


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value
    engine.dispose()


def _add_item(
    session,
    version,
    entity_type,
    canonical_id,
    payload,
    *,
    status=ReviewStatus.PENDING_REVIEW,
    title=None,
    route=None,
):
    item = KnowledgeItem(
        knowledge_version=version,
        canonical_id=canonical_id,
        entity_type=entity_type,
        title=title,
        normalized_title=title.casefold() if title else None,
        route=route,
        content_hash=HASH,
        source_payload={"id": canonical_id, **payload},
        generated_review_status=ReviewStatus.PENDING_REVIEW,
        current_review_status=status,
    )
    session.add(item)
    session.flush()
    return item


def seed(session, *, status=KnowledgeVersionStatus.ACTIVE):
    erp = ERPSystemRecord(
        id="erp:test",
        slug="test",
        name="Test ERP",
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
        schema_version="1.1.0",
        knowledge_version="active-v1",
        canonical_hash=HASH,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=status,
    )
    session.add(version)
    session.flush()
    session.add_all(
        [
            SyncJob(
                knowledge_version_id=version.id,
                target=target,
                status=SyncStatus.SUCCEEDED,
            )
            for target in SyncTarget
        ]
    )

    system = _add_item(
        session,
        version,
        "erp_system",
        "erp:test",
        {"name": "Test ERP"},
        title="Test ERP",
    )
    module = _add_item(
        session,
        version,
        "module",
        "module:root",
        {"erp_id": "erp:test", "parent_module_id": None, "route_prefix": "/root"},
        title="Root",
    )
    module_evidence = _add_item(
        session,
        version,
        "evidence",
        "evidence:module",
        {"source_entity_type": "module", "source_entity_id": "module:root"},
    )
    screen = _add_item(
        session,
        version,
        "screen",
        "screen:a",
        {"erp_id": "erp:test", "module_id": "module:root"},
        title="Screen A",
        route="/root/a",
    )
    field = _add_item(
        session,
        version,
        "field",
        "field:a",
        {"screen_id": "screen:a", "label": "Field A"},
        title="Field A",
    )
    approved_control = _add_item(
        session,
        version,
        "control",
        "control:a",
        {"screen_id": "screen:a", "label": "Control A"},
        status=ReviewStatus.APPROVED,
        title="Control A",
    )
    state_a = _add_item(
        session,
        version,
        "ui_state",
        "ui_state:a",
        {"screen_id": "screen:a", "route": "/root/a", "structural_fingerprint": "a"},
        title="Screen A",
        route="/root/a",
    )
    state_b = _add_item(
        session,
        version,
        "ui_state",
        "ui_state:b",
        {"screen_id": "screen:a", "route": "/root/a", "structural_fingerprint": "b"},
        title="Screen A",
        route="/root/a",
    )
    transition = _add_item(
        session,
        version,
        "transition",
        "transition:a",
        {"source_state_id": "ui_state:a", "target_state_id": "ui_state:b"},
    )
    screen_evidence = _add_item(
        session,
        version,
        "evidence",
        "evidence:screen",
        {"source_entity_type": "field", "source_entity_id": "field:a"},
    )
    orphan = _add_item(
        session,
        version,
        "evidence",
        "evidence:orphan",
        {"source_entity_type": "screen", "source_entity_id": "screen:missing"},
    )
    return {
        "version": version,
        "system": system,
        "module": module,
        "module_evidence": module_evidence,
        "screen": screen,
        "field": field,
        "approved_control": approved_control,
        "state_a": state_a,
        "state_b": state_b,
        "transition": transition,
        "screen_evidence": screen_evidence,
        "orphan": orphan,
    }


def test_packages_group_pending_active_items_by_governed_scope(session):
    with session.begin():
        seeded = seed(session)
    result = StructuralPublicationReviewService(session).build(
        seeded["version"].id,
        pending_only=True,
    )

    assert result.knowledge_version == "active-v1"
    assert result.pending_count == 10
    assert result.publishable_count == 1
    assert result.rejected_count == 0
    assert result.total == 4
    packages = {(item.scope_type, item.scope_id): item for item in result.packages}

    system = packages[("system", "erp:test")]
    assert system.pending_count == 1
    assert system.entity_counts == {"erp_system": 1}

    module = packages[("module", "module:root")]
    assert module.pending_count == 2
    assert module.module_path == ("module:root",)
    assert module.entity_counts == {"evidence": 1, "module": 1}

    screen = packages[("screen", "screen:a")]
    assert screen.pending_count == 6
    assert screen.publishable_count == 1
    assert screen.module_path == ("module:root",)
    assert {item.entity_type for item in screen.review_items} == {
        "evidence",
        "field",
        "screen",
        "transition",
        "ui_state",
    }

    unscoped = packages[("unscoped", "unscoped")]
    assert unscoped.pending_count == 1
    assert unscoped.entity_counts == {"evidence": 1}


def test_approve_pending_is_package_hashed_atomic_and_audited(session):
    with session.begin():
        seeded = seed(session)
    service = StructuralPublicationReviewService(session)
    before = service.build(seeded["version"].id)
    screen = next(item for item in before.packages if item.scope_id == "screen:a")
    session.rollback()

    with session.begin():
        result = service.approve_pending(
            seeded["version"].id,
            scope_type="screen",
            scope_id="screen:a",
            expected_package_hash=screen.package_hash,
            reviewer="reviewer:e2e",
            reason="Baseline estructural inspeccionado por pantalla.",
        )

    assert result.approved_count == 6
    assert result.package.pending_count == 0
    assert result.package.publishable_count == 7
    assert result.package.review_items == ()
    actions = list(session.scalars(select(ReviewAction)))
    assert len(actions) == 6
    assert {str(action.source) for action in actions} == {str(ReviewSource.API)}
    assert {action.reviewer_subject for action in actions} == {"reviewer:e2e"}
    sync_jobs = list(session.scalars(select(SyncJob)))
    assert sync_jobs and all(job.status == SyncStatus.PENDING for job in sync_jobs)

    with pytest.raises(StructuralPublicationReviewConflictError, match="cambió"):
        service.approve_pending(
            seeded["version"].id,
            scope_type="screen",
            scope_id="screen:a",
            expected_package_hash=screen.package_hash,
            reviewer="reviewer:e2e",
            reason="Intento obsoleto.",
        )


def test_only_active_versions_can_use_publication_review(session):
    with session.begin():
        seeded = seed(session, status=KnowledgeVersionStatus.IMPORTED)
    with pytest.raises(StructuralPublicationReviewError, match="ACTIVE"):
        StructuralPublicationReviewService(session).build(seeded["version"].id)


def test_running_projection_blocks_publication_package_review(session):
    with session.begin():
        seeded = seed(session)
        job = session.scalar(
            select(SyncJob).where(SyncJob.target == SyncTarget.NEO4J)
        )
        job.status = SyncStatus.RUNNING
    service = StructuralPublicationReviewService(session)
    package = next(
        item
        for item in service.build(seeded["version"].id).packages
        if item.scope_id == "screen:a"
    )
    session.rollback()

    with pytest.raises(StructuralPublicationReviewConflictError, match="concurrente"):
        with session.begin():
            service.approve_pending(
                seeded["version"].id,
                scope_type="screen",
                scope_id="screen:a",
                expected_package_hash=package.package_hash,
                reviewer="reviewer:e2e",
                reason="No competir con una proyección en curso.",
            )


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


def test_publication_review_api_lists_and_bulk_approves_pending_package(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'publication.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        seeded = seed(session)
        version_id = str(seeded["version"].id)

    app = create_app(
        replace(ApiSettings(), semantic_review_api_enabled=True),
        semantic_review_session_factory=factory,
    )
    client = Client(app)
    response = client.get(
        f"/api/admin/knowledge-versions/{version_id}/publication-review-packages"
        "?pending_only=true&scope_type=module"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 1
    package = data["packages"][0]
    assert package["scope_id"] == "module:root"
    assert package["pending_count"] == 2

    body = {
        "scope_type": "module",
        "scope_id": "module:root",
        "expected_package_hash": package["package_hash"],
        "reviewer_id": "reviewer:e2e",
        "reason": "Paquete de módulo revisado manualmente.",
    }
    approved = client.post(
        f"/api/admin/knowledge-versions/{version_id}/publication-review-packages/approve-pending",
        json=body,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_count"] == 2
    assert approved.json()["package"]["pending_count"] == 0

    stale = client.post(
        f"/api/admin/knowledge-versions/{version_id}/publication-review-packages/approve-pending",
        json=body,
    )
    assert stale.status_code == 409
    assert stale.json()["category"] == "publication_review_conflict"
    engine.dispose()
