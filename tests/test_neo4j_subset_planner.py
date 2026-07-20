from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from scripts.plan_neo4j_subset import build_parser
from src.database.base import Base
from src.database.models import KnowledgeItem, SyncJob
from src.database.services import CanonicalImportService, Neo4jSubsetPlanner
from src.database.services.neo4j_subset_planner import SubsetPlanningError
from src.knowledge.canonical.builder import CanonicalKnowledgeBuilder
from src.knowledge.canonical.exporter import CanonicalKnowledgeExporter
from tests.canonical_fixtures import fictional_artifacts, fictional_profile

ROUTE = "/app/inventory/products"


@pytest.fixture
def planner_session(tmp_path):
    artifacts = fictional_artifacts()
    product = next(
        screen for screen in artifacts["screen_index.json"]["screens"] if screen["route"] == ROUTE
    )
    product["tables"][0]["label"] = "Products"
    product["tables"].append({"label": "Archive", "headers": ["Archived At", "Reference"]})
    artifacts["state_registry.json"]["states"].extend(
        [
            {
                "state_id": "raw:product-secondary-root",
                "route": ROUTE,
                "title": "Products secondary",
                "structural_signature": "secondary-root",
                "metadata": {"depth": 0},
            },
            {
                "state_id": "raw:product-deep",
                "route": ROUTE,
                "title": "Products deep",
                "structural_signature": "deep-state",
                "metadata": {"depth": 2},
            },
        ]
    )
    builder = CanonicalKnowledgeBuilder()
    knowledge = builder.build(fictional_profile(), artifacts)
    CanonicalKnowledgeExporter().export(
        knowledge, tmp_path, build_report=builder.build_report(knowledge)
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            CanonicalImportService(session).import_canonical(
                tmp_path / "knowledge.json", tmp_path / "manifest.json"
            )
        yield session


def test_subset_selection_is_connected_deterministic_and_excludes_other_types(planner_session):
    report = Neo4jSubsetPlanner(planner_session).plan(ROUTE)
    types = [item["entity_type"] for item in report["selected_items"]]
    assert report["erp_id"].startswith("erp:")
    assert "northwind" not in report["erp_id"]
    assert report["screen_route"] == ROUTE
    assert types == [
        "erp_system",
        "module",
        "screen",
        "ui_state",
        "table",
        "table_column",
        "table_column",
    ]
    assert not set(types) & {"field", "control", "link", "event", "transition", "evidence"}
    assert report["selected_items_by_type"] == {
        "erp_system": 1,
        "module": 1,
        "screen": 1,
        "table": 1,
        "table_column": 2,
        "ui_state": 1,
    }
    assert report["expected_nodes"] == 7
    assert report["expected_relationships"] == {
        "HAS_COLUMN": 2,
        "HAS_MODULE": 1,
        "HAS_SCREEN": 2,
        "HAS_STATE": 1,
        "HAS_TABLE": 1,
    }
    assert report["missing_dependencies"] == []
    assert report["privacy_errors"] == {} and report["mapper_errors"] == {}
    assert all(item["privacy_safe"] and item["mapper_ready"] for item in report["selected_items"])


def test_state_table_and_columns_use_deterministic_order(planner_session):
    first = Neo4jSubsetPlanner(planner_session).plan(ROUTE)
    second = Neo4jSubsetPlanner(planner_session).plan(ROUTE)
    assert [item["canonical_id"] for item in first["selected_items"]] == [
        item["canonical_id"] for item in second["selected_items"]
    ]
    state = next(item for item in first["selected_items"] if item["entity_type"] == "ui_state")
    state_rows = list(
        planner_session.scalars(
            select(KnowledgeItem).where(KnowledgeItem.entity_type == "ui_state")
        )
    )
    roots = [
        row
        for row in state_rows
        if row.source_payload.get("route") == ROUTE and row.source_payload.get("depth") == 0
    ]
    assert state["canonical_id"] == min(row.canonical_id for row in roots)
    table = next(item for item in first["selected_items"] if item["entity_type"] == "table")
    assert table["safe_label"] == "Archive"
    columns = [item for item in first["selected_items"] if item["entity_type"] == "table_column"]
    assert [item["safe_label"] for item in columns] == ["Archived At", "Reference"]
    assert all(item["parent_canonical_id"] == table["canonical_id"] for item in columns)


def test_planning_is_read_only_does_not_contact_neo4j_or_change_sync_jobs(
    planner_session, monkeypatch
):
    before_jobs = [
        (str(job.id), str(job.status), job.attempt_count, deepcopy(job.checkpoint))
        for job in planner_session.scalars(select(SyncJob).order_by(SyncJob.target))
    ]
    before_statuses = [
        (str(item.id), str(item.current_review_status), item.review_revision)
        for item in planner_session.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))
    ]
    planner_session.rollback()

    def forbidden_connection(*_args, **_kwargs):
        raise AssertionError("Neo4j no debe ser contactado")

    monkeypatch.setattr("src.graph.client.Neo4jClient.__init__", forbidden_connection)
    report = Neo4jSubsetPlanner(planner_session).plan(ROUTE)
    after_jobs = [
        (str(job.id), str(job.status), job.attempt_count, deepcopy(job.checkpoint))
        for job in planner_session.scalars(select(SyncJob).order_by(SyncJob.target))
    ]
    after_statuses = [
        (str(item.id), str(item.current_review_status), item.review_revision)
        for item in planner_session.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))
    ]
    assert report["current_sync_job"]["status"] == "pending"
    assert before_jobs == after_jobs and before_statuses == after_statuses
    assert not planner_session.new and not planner_session.dirty and not planner_session.deleted


def test_privacy_failure_remains_visible_and_aggregated(planner_session):
    table = planner_session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.entity_type == "table",
            KnowledgeItem.title == "Archive",
        )
    )
    changed = deepcopy(table.source_payload)
    changed["name"] = "001-001-000000001"
    changed["normalized_name"] = "001-001-000000001"
    table.source_payload = changed
    with planner_session.no_autoflush:
        report = Neo4jSubsetPlanner(planner_session).plan(ROUTE)
    planned_table = next(
        item for item in report["selected_items"] if item["canonical_id"] == table.canonical_id
    )
    assert planned_table["privacy_safe"] is False
    assert planned_table["mapper_ready"] is False
    assert planned_table["safe_label"] == ""
    assert report["privacy_errors"] == {"sensitive_value_detected": 1}
    assert report["mapper_errors"] == {"mapping_rejected": 1}
    planner_session.rollback()


def test_privacy_checks_only_mapper_projection_and_safe_report_fields(planner_session):
    erp = planner_session.scalar(
        select(KnowledgeItem).where(KnowledgeItem.entity_type == "erp_system")
    )
    state = planner_session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.entity_type == "ui_state", KnowledgeItem.route == ROUTE
        )
    )
    erp_payload = deepcopy(erp.source_payload)
    erp_payload["base_url"] = "https://synthetic.invalid/1234567890"
    erp_payload["arbitrary_private_metadata"] = "001-001-000000001"
    erp.source_payload = erp_payload
    state_payload = deepcopy(state.source_payload)
    state_payload["exact_fingerprint"] = "001-001-000000001"
    state_payload["structural_fingerprint"] = "1234567890123"
    state.source_payload = state_payload

    with planner_session.no_autoflush:
        report = Neo4jSubsetPlanner(planner_session).plan(ROUTE)

    planned = {
        item["entity_type"]: item
        for item in report["selected_items"]
        if item["entity_type"] in {"erp_system", "ui_state"}
    }
    assert planned["erp_system"]["privacy_safe"] is True
    assert planned["erp_system"]["mapper_ready"] is True
    assert planned["ui_state"]["privacy_safe"] is True
    assert planned["ui_state"]["mapper_ready"] is True
    serialized = str(report)
    assert "base_url" not in serialized
    assert "fingerprint" not in serialized
    assert "source_payload" not in serialized
    assert report["privacy_errors"] == {}
    assert report["mapper_errors"] == {}
    planner_session.rollback()


def test_missing_route_and_inconsistent_parent_fail_safely(planner_session):
    with pytest.raises(SubsetPlanningError, match="No existe una pantalla"):
        Neo4jSubsetPlanner(planner_session).plan("/app/missing")
    planner_session.rollback()
    state = planner_session.scalar(
        select(KnowledgeItem).where(KnowledgeItem.entity_type == "ui_state").limit(1)
    )
    state.parent_canonical_id = "screen:synthetic-mismatch"
    with pytest.raises(SubsetPlanningError, match="parent inconsistente"):
        Neo4jSubsetPlanner(planner_session).plan(state.route)
    planner_session.rollback()


def test_subset_cli_parser():
    args = build_parser().parse_args(["--screen-route", ROUTE, "--pretty"])
    assert args.screen_route == ROUTE and args.pretty is True
