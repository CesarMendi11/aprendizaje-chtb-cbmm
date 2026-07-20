from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from scripts.audit_generalization import audit
from src.database.base import Base
from src.database.models import KnowledgeItem, SyncJob
from src.database.services import CanonicalImportService, Neo4jSubsetPlanner
from src.knowledge.canonical.exporter import CanonicalKnowledgeExporter
from tests.neo4j_generalization_fixtures import NOVA_ROUTE, nova_retail_knowledge


@pytest.fixture
def nova_session(tmp_path):
    knowledge = nova_retail_knowledge()
    CanonicalKnowledgeExporter().export(knowledge, tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            CanonicalImportService(session).import_canonical(
                tmp_path / "knowledge.json", tmp_path / "manifest.json"
            )
        yield session


def test_nova_retail_core_is_generic_and_deterministic(nova_session):
    first = Neo4jSubsetPlanner(nova_session).plan(NOVA_ROUTE)
    second = Neo4jSubsetPlanner(nova_session).plan(NOVA_ROUTE, scope="core")
    assert first["scope"] == "core"
    assert first["erp_id"] == "erp:nova-retail"
    assert first["screen_route"] == NOVA_ROUTE
    assert first["screen_title"] == "Products"
    assert first["selected_items"] == second["selected_items"]
    assert first["selected_items_by_type"] == {
        "erp_system": 1,
        "module": 1,
        "screen": 1,
        "table": 1,
        "table_column": 3,
        "ui_state": 1,
    }
    assert first["expected_relationships"] == {
        "HAS_COLUMN": 3,
        "HAS_MODULE": 1,
        "HAS_SCREEN": 2,
        "HAS_STATE": 1,
        "HAS_TABLE": 1,
    }
    assert all(item["privacy_safe"] and item["mapper_ready"] for item in first["selected_items"])


def test_nova_retail_screen_complete_selects_only_connected_local_knowledge(
    nova_session, monkeypatch
):
    jobs_before = [
        (str(job.id), str(job.status), job.attempt_count, deepcopy(job.checkpoint))
        for job in nova_session.scalars(select(SyncJob).order_by(SyncJob.target))
    ]
    statuses_before = [
        (str(item.id), str(item.current_review_status), item.review_revision)
        for item in nova_session.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))
    ]
    nova_session.rollback()

    def forbidden_connection(*_args, **_kwargs):
        raise AssertionError("Neo4j no debe ser contactado")

    monkeypatch.setattr("src.graph.client.Neo4jClient.__init__", forbidden_connection)
    report = Neo4jSubsetPlanner(nova_session).plan(NOVA_ROUTE, scope="screen-complete")
    selected_ids = {item["canonical_id"] for item in report["selected_items"]}
    omitted = {item["canonical_id"]: item["reason_code"] for item in report["omitted_items"]}

    assert report["selected_items_by_type"] == {
        "control": 3,
        "erp_system": 1,
        "event": 1,
        "field": 2,
        "link": 1,
        "module": 1,
        "screen": 1,
        "table": 1,
        "table_column": 3,
        "transition": 1,
        "ui_state": 2,
    }
    assert "table:nova-products" in selected_ids
    assert {"field:nova-sku", "field:nova-category"} <= selected_ids
    assert {"control:nova-search", "control:nova-add", "control:nova-filter"} <= selected_ids
    assert "link:nova-details" in selected_ids
    assert "event:nova-open-product" in selected_ids
    assert "transition:nova-internal" in selected_ids
    assert omitted == {
        "control:nova-placeholder": "placeholder_control_label",
        "link:nova-global": "global_scope_entity",
        "transition:nova-cross-screen": "cross_screen_transition",
    }
    assert "evidence:nova-products" not in selected_ids
    assert report["expected_relationships"] == {
        "FROM_STATE": 2,
        "HAS_COLUMN": 3,
        "HAS_CONTROL": 3,
        "HAS_EVENT": 1,
        "HAS_FIELD": 2,
        "HAS_LINK": 1,
        "HAS_MODULE": 1,
        "HAS_SCREEN": 2,
        "HAS_STATE": 2,
        "HAS_TABLE": 1,
        "TO_STATE": 1,
        "TRIGGERED_BY": 1,
    }
    assert report["expected_relationships_total"] == sum(
        report["expected_relationships"].values()
    )
    assert report["privacy_errors"] == {} and report["mapper_errors"] == {}

    jobs_after = [
        (str(job.id), str(job.status), job.attempt_count, deepcopy(job.checkpoint))
        for job in nova_session.scalars(select(SyncJob).order_by(SyncJob.target))
    ]
    statuses_after = [
        (str(item.id), str(item.current_review_status), item.review_revision)
        for item in nova_session.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))
    ]
    assert jobs_before == jobs_after and statuses_before == statuses_after
    assert not nova_session.new and not nova_session.dirty and not nova_session.deleted


def test_generalization_audit_has_no_productive_hardcodes():
    report = audit()
    assert report["status"] == "passed"
    assert report["productive_hardcodes"] == []
