from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
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
    RemovalReconciliationDecisionRecord,
    RemovalReconciliationReviewAction,
    RemovalReconciliationReviewSet,
    ReviewAction,
    SyncJob,
)
from src.database.services import (
    CanonicalKnowledgeMaterializer,
    CanonicalReconciliationError,
    CanonicalReconciliationService,
    RemovalReconciliationReviewService,
)
from src.knowledge.canonical.ids import content_hash
from tests.fixtures.removal_review import resolve_all_removals
from tests.governance.test_removal_reconciliation_plan_service import partial_candidate
from tests.governance.test_version_diff_service import seed


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



def _materializable_full_candidate(session, tmp_path):
    active_id, candidate_id, _ = seed(session, tmp_path)
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
    resolve_all_removals(session, candidate_id)
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
        session.query(RemovalReconciliationReviewSet).count(),
        session.query(RemovalReconciliationDecisionRecord).count(),
        session.query(RemovalReconciliationReviewAction).count(),
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
        session.query(RemovalReconciliationReviewSet).count(),
        session.query(RemovalReconciliationDecisionRecord).count(),
        session.query(RemovalReconciliationReviewAction).count(),
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


def test_human_confirmed_remove_changes_reconciled_content(session, tmp_path):
    _, candidate_id, removed_items = _materializable_partial_candidate(session, tmp_path)
    removed_control = removed_items["control"]
    resolve_all_removals(
        session,
        candidate_id,
        confirmed_remove={("control", removed_control.canonical_id)},
    )

    result = CanonicalReconciliationService(session).reconcile(candidate_id)

    assert result.retained_from_active_total == 3
    assert result.confirmed_removed_total == 1
    assert result.unresolved_total == 0
    assert result.reconciled_item_total == result.raw_candidate_item_total + 3
    assert removed_control.canonical_id not in {item.id for item in result.canonical.controls}
    assert all(not value.requires_human_review for value in result.plan.decisions)
    selected = next(
        value
        for value in result.plan.decisions
        if value.entity_type == "control" and value.canonical_id == removed_control.canonical_id
    )
    assert selected.decision == "confirmed_remove"
    assert selected.review_action_id is not None


def test_reconciliation_uses_new_generated_at_without_mutating_raw(session, tmp_path, monkeypatch):
    _, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    resolve_all_removals(session, candidate_id)
    raw_generated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    original_materialize = CanonicalKnowledgeMaterializer.materialize
    original_raw = original_materialize(CanonicalKnowledgeMaterializer(session), candidate_id)

    def materialize_with_old_raw(materializer, version_id, **kwargs):
        canonical = original_materialize(materializer, version_id, **kwargs)
        if str(version_id) == str(candidate_id):
            return canonical.model_copy(update={"generated_at": raw_generated_at})
        return canonical

    monkeypatch.setattr(
        CanonicalKnowledgeMaterializer,
        "materialize",
        materialize_with_old_raw,
    )
    result = CanonicalReconciliationService(session).reconcile(candidate_id)

    raw_after = original_materialize(CanonicalKnowledgeMaterializer(session), candidate_id)
    assert raw_after.generated_at == original_raw.generated_at
    assert result.canonical.generated_at > raw_generated_at
    assert result.canonical.generated_at != raw_generated_at


def test_full_candidate_removals_require_explicit_human_review(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    prepared = RemovalReconciliationReviewService(session).prepare(candidate_id)
    assert prepared.pending_review == prepared.decision_count > 0
    assert all(value.proposed_decision == "unresolved" for value in prepared.decisions)
    with pytest.raises(CanonicalReconciliationError, match="resolver todas"):
        CanonicalReconciliationService(session).reconcile(candidate_id)



def test_full_candidate_reconciliation_applies_explicit_human_decisions(session, tmp_path):
    _, candidate_id, removed_items = _materializable_full_candidate(session, tmp_path)
    removed_control = removed_items["control"]
    resolved = resolve_all_removals(
        session,
        candidate_id,
        confirmed_remove={("control", removed_control.canonical_id)},
    )

    assert resolved.candidate_origin == "full_canonical"
    assert resolved.pending_review == 0
    result = CanonicalReconciliationService(session).reconcile(candidate_id)

    assert result.candidate_origin == "full_canonical"
    assert result.unresolved_total == 0
    assert result.confirmed_removed_total == 1
    assert result.retained_from_active_total == len(removed_items) - 1
    assert removed_control.canonical_id not in {item.id for item in result.canonical.controls}
    assert all(not decision.requires_human_review for decision in result.plan.decisions)
    assert all(decision.review_action_id for decision in result.plan.decisions)

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


def test_duplicate_effective_decision_fails_closed(session, tmp_path, monkeypatch):
    _, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    resolve_all_removals(session, candidate_id)
    plan = RemovalReconciliationReviewService(session).resolved_plan(candidate_id)
    duplicate_plan = replace(plan, decisions=(*plan.decisions, plan.decisions[0]))
    monkeypatch.setattr(
        RemovalReconciliationReviewService,
        "resolved_plan",
        lambda _self, _candidate_version_id: duplicate_plan,
    )

    with pytest.raises(CanonicalReconciliationError, match="decisión duplicada"):
        CanonicalReconciliationService(session).reconcile(candidate_id)


def test_raw_identity_conflicts_with_retain_fails_closed(session, tmp_path, monkeypatch):
    active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    resolve_all_removals(session, candidate_id)
    plan = RemovalReconciliationReviewService(session).resolved_plan(candidate_id)
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
        RemovalReconciliationReviewService,
        "resolved_plan",
        lambda _self, _candidate_version_id: conflicting_plan,
    )

    with pytest.raises(
        CanonicalReconciliationError,
        match="RAW candidate ya contiene una identidad retenida",
    ):
        CanonicalReconciliationService(session).reconcile(candidate_id)
