from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import ERPSystemRecord, ImportRun, KnowledgeItem, KnowledgeVersionRecord
from src.database.services import ModuleSubtreeResolutionError, ModuleSubtreeResolver
from src.knowledge.canonical.enums import ReviewStatus


def _item(version_id, *, canonical_id, entity_type, parent, route=None, title=None, payload=None):
    source_payload = dict(payload or {})
    source_payload.setdefault("id", canonical_id)
    return KnowledgeItem(
        knowledge_version_id=version_id,
        canonical_id=canonical_id,
        entity_type=entity_type,
        parent_canonical_id=parent,
        title=title,
        normalized_title=(title or "").casefold() or None,
        route=route,
        content_hash=f"hash:{canonical_id}",
        source_payload=source_payload,
        generated_review_status=ReviewStatus.APPROVED,
        current_review_status=ReviewStatus.APPROVED,
    )


@pytest.fixture
def resolver_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        erp = ERPSystemRecord(
            id="erp:test",
            slug="erp-test",
            name="ERP Test",
            profile_name="test",
            base_url="http://erp.test",
            safe_metadata={},
        )
        run = ImportRun(
            erp_id=erp.id,
            source_knowledge_path="knowledge.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="v1",
            status=ImportStatus.SUCCEEDED,
        )
        session.add_all([erp, run])
        session.flush()
        version = KnowledgeVersionRecord(
            erp_id=erp.id,
            import_run_id=run.id,
            schema_version="1.1.0",
            knowledge_version="v1",
            canonical_hash="canonical-hash",
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        session.add(version)
        session.flush()

        sales = _item(
            version.id,
            canonical_id="module:sales",
            entity_type="module",
            parent=erp.id,
            title="Sales",
            payload={
                "name": "Sales",
                "depth": 0,
                "navigation_path": ["Sales"],
                "metadata": {
                    "navigation_origin_path": "#sales",
                },
            },
        )
        orders = _item(
            version.id,
            canonical_id="module:orders",
            entity_type="module",
            parent=sales.canonical_id,
            title="Orders",
            payload={
                "name": "Orders",
                "depth": 1,
                "navigation_path": ["Sales", "Orders"],
                "metadata": {
                    "navigation_origin_path": "#sales || #orders",
                },
            },
        )
        tracking = _item(
            version.id,
            canonical_id="module:tracking",
            entity_type="module",
            parent=sales.canonical_id,
            title="Tracking",
            payload={
                "name": "Tracking",
                "depth": 1,
                "navigation_path": ["Sales", "Tracking"],
                "metadata": {
                    "navigation_origin_path": "#sales || #tracking",
                },
            },
        )
        integrations = _item(
            version.id,
            canonical_id="module:integrations",
            entity_type="module",
            parent=tracking.canonical_id,
            title="Integrations",
            payload={
                "name": "Integrations",
                "depth": 2,
                "navigation_path": ["Sales", "Tracking", "Integrations"],
                "metadata": {
                    "navigation_origin_path": "#sales || #tracking || #integrations",
                },
            },
        )
        screens = [
            _item(
                version.id,
                canonical_id="screen:orders",
                entity_type="screen",
                parent=orders.canonical_id,
                route="/sales/orders",
                title="Orders",
            ),
            _item(
                version.id,
                canonical_id="screen:tracking-list",
                entity_type="screen",
                parent=tracking.canonical_id,
                route="/sales/tracking",
                title="Tracking list",
            ),
            _item(
                version.id,
                canonical_id="screen:external",
                entity_type="screen",
                parent=integrations.canonical_id,
                route="/sales/tracking/integrations/external",
                title="External systems",
            ),
            _item(
                version.id,
                canonical_id="screen:unroutable",
                entity_type="screen",
                parent=integrations.canonical_id,
                route=None,
                title="Route pending",
            ),
        ]
        session.add_all([sales, orders, tracking, integrations, *screens])
        session.commit()
        yield session, version
    engine.dispose()


def test_module_subtree_is_recursive_deterministic_and_excludes_siblings(resolver_session):
    session, version = resolver_session
    first = ModuleSubtreeResolver(session).resolve("module:tracking")
    second = ModuleSubtreeResolver(session).resolve("module:tracking")

    assert first == second
    assert first.knowledge_version_id == version.id
    assert first.knowledge_version == "v1"
    assert first.erp_id == "erp:test"
    assert first.root_module_id == "module:tracking"
    assert first.root_module_name == "Tracking"
    assert first.ancestor_module_ids == ("module:sales",)
    assert first.module_ids == (
        "module:tracking",
        "module:integrations",
    )
    assert "module:orders" not in first.module_ids
    assert first.known_screen_ids == (
        "screen:tracking-list",
        "screen:external",
        "screen:unroutable",
    )
    assert first.known_screen_routes == (
        "/sales/tracking",
        "/sales/tracking/integrations/external",
    )
    assert first.unroutable_screen_ids == ("screen:unroutable",)
    assert first.navigation_path == ("Sales", "Tracking")
    assert first.navigation_origin_path == ("#sales", "#tracking")


def test_explicit_active_version_can_be_pinned(resolver_session):
    session, version = resolver_session
    result = ModuleSubtreeResolver(session).resolve(
        "module:tracking",
        knowledge_version_id=version.id,
    )
    assert result.knowledge_version_id == version.id


def test_resolver_rejects_missing_invalid_or_non_active_target(resolver_session):
    session, version = resolver_session
    resolver = ModuleSubtreeResolver(session)

    with pytest.raises(ModuleSubtreeResolutionError, match="identificador canónico"):
        resolver.resolve("tracking")

    with pytest.raises(ModuleSubtreeResolutionError, match="no existe"):
        resolver.resolve("module:missing")

    version.status = KnowledgeVersionStatus.ARCHIVED
    session.commit()
    with pytest.raises(ModuleSubtreeResolutionError, match="ACTIVE"):
        resolver.resolve("module:tracking", knowledge_version_id=version.id)


def test_resolver_fails_closed_on_broken_parent_chain(resolver_session):
    session, _ = resolver_session
    tracking = session.query(KnowledgeItem).filter_by(canonical_id="module:tracking").one()
    tracking.parent_canonical_id = "module:missing-parent"
    session.commit()

    with pytest.raises(ModuleSubtreeResolutionError, match="Ancestro de módulo no encontrado"):
        ModuleSubtreeResolver(session).resolve("module:tracking")


def test_resolver_fails_closed_on_descendant_cycle(resolver_session):
    session, _ = resolver_session
    tracking = session.query(KnowledgeItem).filter_by(canonical_id="module:tracking").one()
    integrations = session.query(KnowledgeItem).filter_by(canonical_id="module:integrations").one()
    tracking.parent_canonical_id = integrations.canonical_id
    session.commit()

    with pytest.raises(ModuleSubtreeResolutionError, match="ciclo"):
        ModuleSubtreeResolver(session).resolve("module:tracking")
