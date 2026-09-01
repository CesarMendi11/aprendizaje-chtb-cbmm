from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from erp_assistant.acquisition.scope.screen_scope_resolver import (
    ScreenScopeResolutionError,
    ScreenScopeResolver,
)
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import ImportStatus, KnowledgeVersionStatus
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
)
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.services.knowledge_review_service import KnowledgeReviewService


def _version(session: Session, *, name: str, status: KnowledgeVersionStatus):
    erp = session.get(ERPSystemRecord, "erp:screen-test")
    if erp is None:
        erp = ERPSystemRecord(
            id="erp:screen-test",
            slug="screen-test",
            name="Screen Test ERP",
            profile_name="test",
            safe_metadata={},
        )
        session.add(erp)
        session.flush()
    run = ImportRun(
        erp_id=erp.id,
        source_knowledge_path=f"{name}.json",
        source_manifest_path=f"{name}-manifest.json",
        requested_knowledge_version=name,
        status=ImportStatus.SUCCEEDED,
    )
    session.add(run)
    session.flush()
    version = KnowledgeVersionRecord(
        erp_id=erp.id,
        import_run_id=run.id,
        schema_version="1.1.0",
        knowledge_version=name,
        canonical_hash=(name[0] * 64),
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=status,
    )
    session.add(version)
    session.flush()
    screen = KnowledgeItem(
        knowledge_version_id=version.id,
        canonical_id="screen:tracking",
        entity_type="screen",
        parent_canonical_id="module:tracking",
        title="Tracking",
        normalized_title="tracking",
        route="/admin/tracking",
        content_hash="c" * 64,
        source_payload={
            "id": "screen:tracking",
            "erp_id": erp.id,
            "route": "/admin/tracking",
            "module_id": "module:tracking",
            "title": "Tracking",
            "normalized_title": "tracking",
        },
        generated_review_status=ReviewStatus.APPROVED,
        current_review_status=ReviewStatus.APPROVED,
    )
    session.add(screen)
    session.flush()
    return version


def test_screen_scope_resolves_exact_active_route_and_pins_version():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        active = _version(session, name="active-v1", status=KnowledgeVersionStatus.ACTIVE)
        resolved = ScreenScopeResolver(session).resolve("/admin/tracking")
        assert resolved.knowledge_version_id == active.id
        assert resolved.knowledge_version == "active-v1"
        assert resolved.erp_id == "erp:screen-test"
        assert resolved.screen_id == "screen:tracking"
        assert resolved.screen_title == "Tracking"
        assert resolved.route == "/admin/tracking"
        assert resolved.module_id == "module:tracking"
    engine.dispose()


def test_screen_scope_rejects_unknown_or_non_active_pin():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        archived = _version(
            session,
            name="archived-v1",
            status=KnowledgeVersionStatus.ARCHIVED,
        )
        service = ScreenScopeResolver(session)
        with pytest.raises(ScreenScopeResolutionError, match="ACTIVE"):
            service.resolve("/admin/tracking")
        with pytest.raises(ScreenScopeResolutionError, match="ACTIVE indicada"):
            service.resolve("/admin/tracking", knowledge_version_id=archived.id)
    engine.dispose()


def test_screen_scope_uses_effective_corrected_title_for_direct_crawl_identity():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        _version(session, name="active-v1", status=KnowledgeVersionStatus.ACTIVE)
        screen = session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.canonical_id == "screen:tracking",
            )
        )
        assert screen is not None
        corrected = dict(screen.source_payload)
        corrected["title"] = "Tracking corregido"
        corrected["normalized_title"] = "tracking corregido"
        KnowledgeReviewService(session).correct(
            screen.id,
            corrected,
            notes="corrección de título",
        )

        resolved = ScreenScopeResolver(session).resolve("/admin/tracking")

        assert resolved.screen_title == "Tracking corregido"
    engine.dispose()
