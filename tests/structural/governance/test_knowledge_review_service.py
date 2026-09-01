from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

import erp_assistant.persistence.postgres.models  # noqa: F401
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import SyncStatus, SyncTarget
from erp_assistant.persistence.postgres.models import KnowledgeItem, SyncJob
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.services.canonical_import_service import CanonicalImportService
from erp_assistant.structural.services.effective_knowledge_service import EffectiveKnowledgeService
from erp_assistant.structural.services.knowledge_review_service import KnowledgeReviewService
from tests.fixtures.canonical import exported_fictional_canonical


@pytest.fixture
def reviewed(tmp_path):
    canonical_dir = exported_fictional_canonical(tmp_path / "canonical")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            CanonicalImportService(session).import_canonical(
                canonical_dir / "knowledge.json", canonical_dir / "manifest.json"
            )
        session.rollback()
        item = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.entity_type == "screen").limit(1)
        )
        session.rollback()
        yield session, item


def test_approve_reject_reset_and_append_only_history(reviewed):
    session, item = reviewed
    service = KnowledgeReviewService(session)
    with session.begin():
        service.approve(item.id, expected_revision=0)
    with session.begin():
        service.reject(item.id, notes="Información incorrecta", expected_revision=1)
    with session.begin():
        service.reset_to_pending(item.id, expected_revision=2)
    assert item.current_review_status == ReviewStatus.PENDING_REVIEW
    assert item.review_revision == 3
    assert len(service.get_review_history(item.id)) == 3


def test_describe_empty_history_is_structured_and_source_is_immutable(reviewed):
    session, item = reviewed
    original = dict(item.source_payload)
    description = EffectiveKnowledgeService(session).describe(item.id)
    assert description["history"] == []
    assert description["source_payload"] == original
    assert item.source_payload == original


def test_describe_approve_and_reset_history_is_safe_and_ordered(reviewed):
    session, item = reviewed
    original = dict(item.source_payload)
    original_generated_status = original.get("review_status")
    service = KnowledgeReviewService(session)
    session.rollback()
    with session.begin():
        service.approve(item.id, reviewer="synthetic-reviewer", notes="Aprobación sintética")
    approved = EffectiveKnowledgeService(session).describe(item.id)
    assert len(approved["history"]) == 1
    assert approved["history"][0]["action"] == "approve"
    assert approved["history"][0]["new_status"] == "approved"
    assert item.current_review_status == ReviewStatus.APPROVED
    session.rollback()
    with session.begin():
        service.reset_to_pending(item.id, reviewer="synthetic-reviewer")
    description = EffectiveKnowledgeService(session).describe(item.id)
    assert [action["action"] for action in description["history"]] == [
        "approve",
        "reset_to_pending",
    ]
    assert [action["new_status"] for action in description["history"]] == [
        "approved",
        "pending_review",
    ]
    assert all(
        set(action) == {"id", "action", "previous_status", "new_status", "source", "created_at"}
        for action in description["history"]
    )
    assert "ReviewAction object at" not in str(description)
    assert item.current_review_status == ReviewStatus.PENDING_REVIEW
    assert item.source_payload == original
    assert item.source_payload.get("review_status") == original_generated_status


def test_describe_many_batches_review_history_without_refetching_items(reviewed):
    session, item = reviewed
    session.rollback()
    with session.begin():
        KnowledgeReviewService(session).approve(item.id)

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.casefold())

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        descriptions = EffectiveKnowledgeService(session).describe_many([item])
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    description = descriptions[item.id]
    assert description["effective_payload"] == item.source_payload
    assert [row["action"] for row in description["history"]] == ["approve"]
    assert sum("review_actions" in statement for statement in statements) == 1
    assert sum("knowledge_items" in statement for statement in statements) == 0


def test_invalid_transition_and_concurrent_revision(reviewed):
    session, item = reviewed
    service = KnowledgeReviewService(session)
    with session.begin():
        service.approve(item.id)
    with pytest.raises(ValueError, match="Transición"):
        with session.begin():
            service.approve(item.id)
    with pytest.raises(ValueError, match="concurrente"):
        with session.begin():
            service.correct(item.id, item.source_payload, notes="ajuste", expected_revision=0)


def test_correction_preserves_source_and_effective_payload(reviewed):
    session, item = reviewed
    source = dict(item.source_payload)
    correction = dict(source)
    correction.pop("review_status", None)
    correction.pop("reviewed_at", None)
    correction.pop("reviewed_by", None)
    correction.pop("review_notes", None)
    correction["description"] = "Descripción funcional revisada"
    session.rollback()
    with session.begin():
        KnowledgeReviewService(session).correct(
            item.id, correction, reviewer="operator", notes="Corrección controlada"
        )
    effective = EffectiveKnowledgeService(session).describe(item.id)
    assert item.source_payload == source
    assert effective["was_corrected"] is True
    assert effective["effective_payload"]["description"] == "Descripción funcional revisada"


@pytest.mark.parametrize(
    "patch",
    [
        {"id": "different"},
        {"password": "secret"},
        {"description": "<script>alert(1)</script>"},
    ],
)
def test_invalid_corrections_are_rejected(reviewed, patch):
    session, item = reviewed
    payload = {
        key: value
        for key, value in item.source_payload.items()
        if key not in {"review_status", "reviewed_at", "reviewed_by", "review_notes"}
    }
    payload.update(patch)
    session.rollback()
    with pytest.raises(ValueError):
        with session.begin():
            KnowledgeReviewService(session).correct(item.id, payload, notes="invalid")


def test_only_approved_or_corrected_are_projected(reviewed):
    session, item = reviewed
    with session.begin():
        KnowledgeReviewService(session).approve(item.id)
    projection = EffectiveKnowledgeService(session).projection_for_sync(
        version_id=item.knowledge_version_id
    )
    assert [entry["canonical_id"] for entry in projection] == [item.canonical_id]


def test_active_projection_jobs_are_invalidated_when_review_changes_projection(reviewed):
    session, item = reviewed
    with session.begin():
        jobs = list(session.scalars(select(SyncJob)))
        for job in jobs:
            job.status = SyncStatus.SUCCEEDED
            job.attempt_count = 3
            job.checkpoint = {"projection": "old"}
            job.error_summary = "old error"

    with session.begin():
        KnowledgeReviewService(session).approve(item.id)

    jobs = list(session.scalars(select(SyncJob).order_by(SyncJob.target)))
    assert {job.target for job in jobs} == {SyncTarget.NEO4J, SyncTarget.CHROMADB}
    assert all(job.status == SyncStatus.PENDING for job in jobs)
    assert all(job.attempt_count == 3 for job in jobs)
    assert all(job.started_at is None and job.finished_at is None for job in jobs)
    assert all(job.checkpoint is None and job.error_summary is None for job in jobs)


def test_non_publishable_review_change_does_not_invalidate_projection_jobs(reviewed):
    session, item = reviewed
    with session.begin():
        for job in session.scalars(select(SyncJob)):
            job.status = SyncStatus.SUCCEEDED

    with session.begin():
        KnowledgeReviewService(session).reject(item.id, notes="No publicar")

    jobs = list(session.scalars(select(SyncJob)))
    assert jobs and all(job.status == SyncStatus.SUCCEEDED for job in jobs)


def test_running_projection_blocks_projection_affecting_review(reviewed):
    session, item = reviewed
    with session.begin():
        job = session.scalar(select(SyncJob).where(SyncJob.target == SyncTarget.NEO4J))
        job.status = SyncStatus.RUNNING

    with pytest.raises(ValueError, match="concurrente"):
        with session.begin():
            KnowledgeReviewService(session).approve(item.id)

    session.rollback()
    session.refresh(item)
    assert item.current_review_status == ReviewStatus.PENDING_REVIEW


def test_module_parent_relation_cannot_be_changed_by_human_correction(reviewed):
    session, _ = reviewed
    module = session.scalar(
        select(KnowledgeItem).where(KnowledgeItem.entity_type == "module").limit(1)
    )
    payload = {
        key: value
        for key, value in module.source_payload.items()
        if key not in {"review_status", "reviewed_at", "reviewed_by", "review_notes"}
    }
    payload["parent_module_id"] = "module:other"

    session.rollback()
    with pytest.raises(ValueError, match="parent_module_id"):
        with session.begin():
            KnowledgeReviewService(session).correct(
                module.id,
                payload,
                notes="No debe permitirse cambiar la jerarquía",
            )
