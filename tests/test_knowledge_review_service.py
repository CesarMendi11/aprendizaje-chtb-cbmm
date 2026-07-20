from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.database.base import Base
import src.database.models  # noqa: F401
from src.database.models import KnowledgeItem, ReviewAction
from src.database.services import (
    CanonicalImportService, EffectiveKnowledgeService, KnowledgeReviewService
)
from src.knowledge.canonical.enums import ReviewStatus

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/processed/canonical"


@pytest.fixture
def reviewed():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            CanonicalImportService(session).import_canonical(
                CANONICAL / "knowledge.json", CANONICAL / "manifest.json"
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
        "approve", "reset_to_pending"
    ]
    assert [action["new_status"] for action in description["history"]] == [
        "approved", "pending_review"
    ]
    assert all(set(action) == {
        "id", "action", "previous_status", "new_status", "source", "created_at"
    } for action in description["history"])
    assert "ReviewAction object at" not in str(description)
    assert item.current_review_status == ReviewStatus.PENDING_REVIEW
    assert item.source_payload == original
    assert item.source_payload.get("review_status") == original_generated_status


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
            service.correct(
                item.id, item.source_payload, notes="ajuste", expected_revision=0
            )


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


@pytest.mark.parametrize("patch", [
    {"id": "different"},
    {"password": "secret"},
    {"description": "<script>alert(1)</script>"},
])
def test_invalid_corrections_are_rejected(reviewed, patch):
    session, item = reviewed
    payload = {
        key: value for key, value in item.source_payload.items()
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
