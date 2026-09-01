from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import erp_assistant.persistence.postgres.models  # noqa: F401
from erp_assistant.api.app import create_app
from erp_assistant.config.api_settings import ApiSettings
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    RemovalReconciliationDecisionType,
)
from erp_assistant.persistence.postgres.models import (
    PipelineJob,
    RemovalReconciliationDecisionRecord,
    RemovalReconciliationReviewAction,
    RemovalReconciliationReviewSet,
)
from erp_assistant.structural.services.removal_reconciliation_review_service import (
    RemovalReconciliationReviewError,
    RemovalReconciliationReviewNotPreparedError,
    RemovalReconciliationReviewService,
)
from tests.structural.governance.test_removal_reconciliation_plan_service import partial_candidate


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def test_prepare_is_idempotent_and_materializes_exact_pending_set(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    service = RemovalReconciliationReviewService(session)

    first = service.prepare(candidate_id)
    second = service.prepare(candidate_id)

    assert first.id == second.id
    assert first.plan_hash == second.plan_hash
    assert first.decision_count == first.pending_review > 0
    assert first.retain_from_active == 0
    assert first.confirmed_remove == 0
    assert all(value.current_decision is None for value in first.decisions)
    assert session.query(RemovalReconciliationReviewSet).count() == 1
    assert session.query(RemovalReconciliationDecisionRecord).count() == first.decision_count
    assert session.query(RemovalReconciliationReviewAction).count() == 0


def test_resolved_plan_requires_explicit_human_decisions_and_preserves_history(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    service = RemovalReconciliationReviewService(session)
    prepared = service.prepare(candidate_id)
    decision = prepared.decisions[0]

    with pytest.raises(RemovalReconciliationReviewError, match="resolver todas"):
        service.resolved_plan(candidate_id)

    retained = service.confirm_retain(
        decision.id,
        reviewer="reviewer:alice",
        reason="La ausencia no demuestra borrado.",
        expected_revision=0,
    )
    assert retained.current_decision == "retain_from_active"
    assert retained.review_revision == 1
    plan = service.resolved_plan(candidate_id)
    assert plan.unresolved_total == 0
    assert plan.retain_from_active_total == 1
    assert plan.confirmed_removed_total == 0
    assert plan.decisions[0].requires_human_review is False
    assert plan.decisions[0].review_set_id == prepared.id
    assert plan.decisions[0].review_decision_id == decision.id
    assert plan.decisions[0].review_action_id is not None
    assert plan.decisions[0].review_revision == 1

    history = service.history(decision.id)
    assert len(history) == 1
    assert history[0].reviewer_subject == "reviewer:alice"
    assert history[0].new_decision == RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE


def test_confirm_remove_reset_and_concurrency_contract(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    service = RemovalReconciliationReviewService(session)
    decision = service.prepare(candidate_id).decisions[0]

    changed = service.confirm_remove(
        decision.id,
        reviewer="reviewer:bob",
        reason="Borrado confirmado manualmente.",
        expected_revision=0,
    )
    assert changed.current_decision == "confirmed_remove"
    assert service.resolved_plan(candidate_id).confirmed_removed_total == 1

    with pytest.raises(RemovalReconciliationReviewError, match="Conflicto"):
        service.reset_to_pending(
            decision.id,
            reviewer="reviewer:bob",
            reason="stale",
            expected_revision=0,
        )
    pending = service.reset_to_pending(
        decision.id,
        reviewer="reviewer:bob",
        reason="Reabrir para nueva evidencia.",
        expected_revision=1,
    )
    assert pending.current_decision is None
    assert pending.review_revision == 2
    with pytest.raises(RemovalReconciliationReviewError, match="resolver todas"):
        service.resolved_plan(candidate_id)


def test_consumed_review_set_is_frozen(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    service = RemovalReconciliationReviewService(session)
    prepared = service.prepare(candidate_id)
    decision = prepared.decisions[0]
    service.confirm_retain(
        decision.id,
        reviewer="reviewer:alice",
        reason="Confirmado antes de reconciliar.",
        expected_revision=0,
    )
    session.add(
        PipelineJob(
            kind=PipelineJobKind.CANONICAL_RECONCILIATION,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.VERSION,
            erp_id=prepared.erp_id,
            knowledge_version_id=candidate_id,
            request_source="test",
            parameters={},
            result_payload={
                "decision_set_hash": "x" * 64,
                "decisions": [{"review_set_id": prepared.id}],
            },
        )
    )
    session.flush()

    with pytest.raises(RemovalReconciliationReviewError, match="ya fue consumido"):
        service.reset_to_pending(
            decision.id,
            reviewer="reviewer:alice",
            reason="No debe permitirse después del consumo.",
            expected_revision=1,
        )


def test_review_set_fails_closed_if_persisted_provenance_is_tampered(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    service = RemovalReconciliationReviewService(session)
    prepared = service.prepare(candidate_id)
    decision = session.get(
        RemovalReconciliationDecisionRecord,
        __import__("uuid").UUID(prepared.decisions[0].id),
    )

    # Bypass ORM immutability deliberately to simulate storage tampering.
    session.execute(
        RemovalReconciliationDecisionRecord.__table__.update()
        .where(RemovalReconciliationDecisionRecord.id == decision.id)
        .values(decision_fingerprint="0" * 64)
    )
    session.expire_all()
    with pytest.raises(RemovalReconciliationReviewError, match="provenance RAW"):
        service.get(candidate_id)


def test_action_history_tampering_is_detected_before_next_transition(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    service = RemovalReconciliationReviewService(session)
    decision = service.prepare(candidate_id).decisions[0]
    service.confirm_retain(
        decision.id,
        reviewer="reviewer:alice",
        reason="Confirmación inicial.",
        expected_revision=0,
    )
    decision_uuid = __import__("uuid").UUID(decision.id)
    action = session.scalar(
        select(RemovalReconciliationReviewAction).where(
            RemovalReconciliationReviewAction.decision_id == decision_uuid
        )
    )
    session.execute(
        RemovalReconciliationReviewAction.__table__.update()
        .where(RemovalReconciliationReviewAction.id == action.id)
        .values(decision_fingerprint="0" * 64)
    )
    session.expire_all()

    with pytest.raises(RemovalReconciliationReviewError, match="acción de removal review"):
        service.reset_to_pending(
            decision.id,
            reviewer="reviewer:alice",
            reason="No debe aceptar un historial alterado.",
            expected_revision=1,
        )


def test_get_requires_explicit_prepare(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    with pytest.raises(RemovalReconciliationReviewNotPreparedError):
        RemovalReconciliationReviewService(session).get(candidate_id)


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


def test_removal_review_api_prepare_review_history_and_stale_revision(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'removal-review.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, candidate_id = partial_candidate(session, tmp_path)
    app = create_app(
        replace(ApiSettings(), semantic_review_api_enabled=True),
        semantic_review_session_factory=factory,
    )
    client = Client(app)

    missing = client.get(f"/api/admin/removal-reconciliation-reviews/{candidate_id}")
    assert missing.status_code == 409
    prepared = client.post(f"/api/admin/removal-reconciliation-reviews/{candidate_id}/prepare")
    assert prepared.status_code == 200, prepared.text
    data = prepared.json()
    assert data["decision_count"] == data["pending_review"] > 0
    decision = data["decisions"][0]
    body = {
        "reviewer_id": "reviewer:api",
        "reason": "Confirmación humana explícita.",
        "expected_revision": 0,
    }
    reviewed = client.post(
        f"/api/admin/removal-reconciliation-reviews/decisions/{decision['id']}/confirm-retain",
        json=body,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["current_decision"] == "retain_from_active"
    stale = client.post(
        f"/api/admin/removal-reconciliation-reviews/decisions/{decision['id']}/confirm-remove",
        json=body,
    )
    assert stale.status_code == 409
    history = client.get(
        f"/api/admin/removal-reconciliation-reviews/decisions/{decision['id']}/history"
    )
    assert history.status_code == 200
    assert len(history.json()["actions"]) == 1
    assert history.json()["actions"][0]["source"] == "api"
    engine.dispose()
