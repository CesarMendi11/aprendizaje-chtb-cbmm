from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    PipelineJob,
    ReviewAction,
    SyncJob,
)
from src.database.services import (
    CanonicalKnowledgeMaterializer,
    CanonicalReconciliationError,
    CanonicalReconciliationService,
)
from src.database.services.removal_reconciliation_plan_service import (
    RemovalReconciliationPlanService,
)
from src.knowledge.canonical.ids import content_hash
from tests.test_removal_reconciliation_plan_service import partial_candidate
from tests.test_version_diff_service import seed


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def _materializable_partial_candidate(session, tmp_path):
    active_id, candidate_id = partial_candidate(session, tmp_path)
    with session.begin():
        active_screens = {
            item.canonical_id: item
            for item in session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == active_id,
                    KnowledgeItem.entity_type == "screen",
                )
            )
        }
        candidate_screen_ids = set(
            session.scalars(
                select(KnowledgeItem.canonical_id).where(
                    KnowledgeItem.knowledge_version_id == candidate_id,
                    KnowledgeItem.entity_type == "screen",
                )
            )
        )
        for screen_id in active_screens.keys() - candidate_screen_ids:
            source = active_screens[screen_id]
            session.add(
                KnowledgeItem(
                    knowledge_version_id=candidate_id,
                    canonical_id=source.canonical_id,
                    entity_type=source.entity_type,
                    parent_canonical_id=source.parent_canonical_id,
                    title=source.title,
                    normalized_title=source.normalized_title,
                    route=source.route,
                    content_hash=source.content_hash,
                    source_payload=source.source_payload,
                    generated_review_status=source.generated_review_status,
                    current_review_status=source.current_review_status,
                )
            )
        for item in session.scalars(
            select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == candidate_id)
        ):
            if item.source_payload.get("id") != item.canonical_id:
                session.delete(item)
        removed_items = {}
        for entity_type in ("control", "table_column", "ui_state", "transition"):
            item = session.scalar(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == candidate_id,
                    KnowledgeItem.entity_type == entity_type,
                )
            )
            assert item is not None
            removed_items[entity_type] = item
            session.delete(item)
    return active_id, candidate_id, removed_items


def test_reconciliation_materializes_in_memory_and_is_read_only(session, tmp_path):
    active_id, candidate_id, removed_items = _materializable_partial_candidate(session, tmp_path)
    control = removed_items["control"]
    column = removed_items["table_column"]
    state = removed_items["ui_state"]
    transition = removed_items["transition"]
    before = (
        [(item.id, item.status) for item in session.scalars(select(KnowledgeVersionRecord))],
        [
            (item.id, item.content_hash, item.current_review_status, item.review_revision)
            for item in session.scalars(select(KnowledgeItem))
        ],
        session.query(ReviewAction).count(),
        session.query(PipelineJob).count(),
        session.query(SyncJob).count(),
    )
    raw_before = (
        CanonicalKnowledgeMaterializer(session).materialize(candidate_id).model_dump(mode="json")
    )
    result = CanonicalReconciliationService(session).reconcile(candidate_id)
    after = (
        [(item.id, item.status) for item in session.scalars(select(KnowledgeVersionRecord))],
        [
            (item.id, item.content_hash, item.current_review_status, item.review_revision)
            for item in session.scalars(select(KnowledgeItem))
        ],
        session.query(ReviewAction).count(),
        session.query(PipelineJob).count(),
        session.query(SyncJob).count(),
    )
    assert before == after
    assert (
        CanonicalKnowledgeMaterializer(session).materialize(candidate_id).model_dump(mode="json")
        == raw_before
    )
    assert (
        result.reconciled_item_total
        == result.raw_candidate_item_total + result.retained_from_active_total
    )
    raw_candidate_ids = {
        collection: {item["id"] for item in raw_before[collection]}
        for collection in ("controls", "table_columns", "ui_states", "transitions")
    }
    reconciled_ids = {
        collection: {item.id for item in getattr(result.canonical, collection)}
        for collection in ("controls", "table_columns", "ui_states", "transitions")
    }
    assert control.canonical_id not in raw_candidate_ids["controls"]
    assert column.canonical_id not in raw_candidate_ids["table_columns"]
    assert state.canonical_id not in raw_candidate_ids["ui_states"]
    assert transition.canonical_id not in raw_candidate_ids["transitions"]
    assert control.canonical_id in reconciled_ids["controls"]
    assert column.canonical_id in reconciled_ids["table_columns"]
    assert state.canonical_id in reconciled_ids["ui_states"]
    assert transition.canonical_id in reconciled_ids["transitions"]
    assert result.retained_from_active_total == 4
    assert result.confirmed_removed_total == 0
    functional = {
        "erp_system": result.canonical.erp_system.model_dump(mode="json"),
        **{
            collection: [
                item.model_dump(mode="json") for item in getattr(result.canonical, collection)
            ]
            for collection in (
                "modules",
                "screens",
                "ui_states",
                "fields",
                "controls",
                "tables",
                "table_columns",
                "links",
                "events",
                "transitions",
            )
        },
    }
    assert result.canonical.knowledge_version == content_hash(functional)[:16]
    assert result.canonical.knowledge_version != raw_before["knowledge_version"]
    assert result.canonical.generator_version == "canonical-reconciliation-1.0.0"
    active_payloads = {
        (item.entity_type, item.canonical_id): item.source_payload
        for item in session.scalars(
            select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == active_id)
        )
    }
    for collection, entity_type, item_id in (
        ("controls", "control", control.canonical_id),
        ("table_columns", "table_column", column.canonical_id),
        ("ui_states", "ui_state", state.canonical_id),
        ("transitions", "transition", transition.canonical_id),
    ):
        materialized = next(
            item for item in getattr(result.canonical, collection) if item.id == item_id
        )
        assert materialized.model_dump(mode="json") == active_payloads[(entity_type, item_id)]
    assert result.unresolved_total == 0


def test_unresolved_plan_fails_closed_before_materialization(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    with pytest.raises(CanonicalReconciliationError, match="UNRESOLVED"):
        CanonicalReconciliationService(session).reconcile(candidate_id)


def test_active_retention_identity_mismatches_fail_closed(session, tmp_path):
    active_id, _ = partial_candidate(session, tmp_path)
    item = session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == active_id,
            KnowledgeItem.entity_type == "control",
        )
    )
    service = CanonicalReconciliationService(session)
    for active_item_id, entity_type, canonical_id in (
        (uuid.uuid4(), item.entity_type, item.canonical_id),
        (item.id, "field", item.canonical_id),
        (item.id, item.entity_type, "control:wrong"),
    ):
        decision = SimpleNamespace(
            active_item_id=str(active_item_id),
            entity_type=entity_type,
            canonical_id=canonical_id,
        )
        with pytest.raises(CanonicalReconciliationError, match="exactamente un item ACTIVE"):
            service._active_item(active_id, decision)


def test_duplicate_plan_decision_fails_closed(session, tmp_path, monkeypatch):
    _, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    plan = RemovalReconciliationPlanService(session).build(candidate_id)
    duplicate_plan = replace(plan, decisions=(*plan.decisions, plan.decisions[0]))
    monkeypatch.setattr(
        RemovalReconciliationPlanService,
        "build",
        lambda _self, _candidate_version_id: duplicate_plan,
    )

    with pytest.raises(CanonicalReconciliationError, match="decisión duplicada"):
        CanonicalReconciliationService(session).reconcile(candidate_id)


def test_raw_identity_conflicts_with_retain_fails_closed(session, tmp_path, monkeypatch):
    active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    plan = RemovalReconciliationPlanService(session).build(candidate_id)
    raw_item = session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == candidate_id,
            KnowledgeItem.entity_type == "screen",
        )
    )
    assert raw_item is not None
    active_item = session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == active_id,
            KnowledgeItem.entity_type == raw_item.entity_type,
            KnowledgeItem.canonical_id == raw_item.canonical_id,
        )
    )
    assert active_item is not None
    conflicting_decision = replace(
        plan.decisions[0],
        entity_type=raw_item.entity_type,
        canonical_id=raw_item.canonical_id,
        active_item_id=str(active_item.id),
    )
    conflicting_plan = replace(
        plan,
        decisions=(conflicting_decision, *plan.decisions[1:]),
    )
    monkeypatch.setattr(
        RemovalReconciliationPlanService,
        "build",
        lambda _self, _candidate_version_id: conflicting_plan,
    )

    with pytest.raises(
        CanonicalReconciliationError,
        match="RAW candidate ya contiene una identidad retenida",
    ):
        CanonicalReconciliationService(session).reconcile(candidate_id)
