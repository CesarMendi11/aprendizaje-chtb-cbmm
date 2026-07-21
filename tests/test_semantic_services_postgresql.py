from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.database.enums import ImportStatus, KnowledgeVersionStatus, SemanticType
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from src.database.repositories import SemanticProposalRepository
from src.database.services import SemanticProposalService, SemanticReviewService
from src.database.services.semantic_exceptions import (
    SemanticIdentityCollisionError,
    SemanticPayloadError,
    SemanticRevisionConflictError,
    SemanticVersionMismatchError,
)
from src.knowledge.canonical.enums import ReviewStatus

HASH_A = "a" * 64
HASH_B = "b" * 64


def _test_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    database = urlsplit(url).path.lstrip("/").casefold()
    if "semantic_test" not in database:
        pytest.fail("TEST_DATABASE_URL no apunta a una base temporal con marcador seguro")
    return url


@pytest.fixture(scope="module")
def pg_engine():
    engine = sa.create_engine(_test_url(), pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("Las pruebas de servicio semántico requieren PostgreSQL")
    yield engine
    engine.dispose()


def seed_screen(session: Session):
    suffix = uuid.uuid4().hex[:20].translate(str.maketrans("0123456789", "abcdefghij"))
    erp = ERPSystemRecord(
        id=f"erp:test:{suffix}",
        slug=f"test-{suffix}",
        name="Synthetic ERP",
        profile_name="semantic-service-test",
        safe_metadata={},
    )
    run = ImportRun(
        erp=erp,
        source_knowledge_path="synthetic/knowledge.json",
        source_manifest_path="synthetic/manifest.json",
        requested_knowledge_version=suffix,
        status=ImportStatus.SUCCEEDED,
        source_hashes={},
    )
    version = KnowledgeVersionRecord(
        erp=erp,
        import_run=run,
        schema_version="1.0.0",
        knowledge_version=suffix,
        canonical_hash=HASH_A,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=KnowledgeVersionStatus.IMPORTED,
    )
    screen = KnowledgeItem(
        knowledge_version=version,
        canonical_id=f"screen:test:{suffix}",
        entity_type="screen",
        title="Synthetic Screen",
        normalized_title="synthetic screen",
        route=f"/test/{suffix}",
        content_hash=HASH_A,
        source_payload={"id": f"screen:test:{suffix}"},
        generated_review_status=ReviewStatus.APPROVED,
        current_review_status=ReviewStatus.APPROVED,
    )
    session.add(screen)
    session.flush()
    return version, screen


def create_proposal(session: Session, version, screen, *, prompt_hash=HASH_B):
    return SemanticProposalService(session).create_pending_proposal(
        knowledge_version_id=version.id,
        screen_knowledge_item_id=screen.id,
        semantic_type=SemanticType.SCREEN_PURPOSE,
        source_payload={"purpose_summary": "Permite una consulta sintética."},
        evidence_payload={"screen_title": "Synthetic Screen"},
        evidence_ids=[f"evidence:test:{screen.canonical_id.rsplit(':', 1)[-1]}"],
        generation_model="synthetic-model",
        prompt_version="screen-purpose-v1",
        prompt_hash=prompt_hash,
        generation_parameters={"temperature": 0, "stream": False},
    )


def run_creation_race(pg_engine, monkeypatch, *, incompatible: bool):
    with Session(pg_engine, expire_on_commit=False) as setup, setup.begin():
        version, screen = seed_screen(setup)
        version_id = version.id
        screen_id = screen.id

    barrier = threading.Barrier(2)
    winner_committed = threading.Event()
    thread_state = threading.local()
    outcomes = {}
    failures = []
    soft_rollbacks = {"a": 0, "b": 0}
    original_lookup = SemanticProposalRepository.get_by_generation_identity

    def synchronized_lookup(repository, **identity):
        existing = original_lookup(repository, **identity)
        if existing is None and not getattr(thread_state, "checked_absence", False):
            thread_state.checked_absence = True
            barrier.wait(timeout=10)
            if threading.current_thread().name == "semantic-race-b":
                assert winner_committed.wait(timeout=10)
        return existing

    monkeypatch.setattr(
        SemanticProposalRepository,
        "get_by_generation_identity",
        synchronized_lookup,
    )

    def worker(label: str):
        session = Session(pg_engine, expire_on_commit=False)

        def count_soft_rollback(*_args):
            soft_rollbacks[label] += 1

        sa.event.listen(session, "after_soft_rollback", count_soft_rollback)
        try:
            with session.begin():
                marker = ERPSystemRecord(
                    id=f"erp:semantic-race-marker-{label}-{uuid.uuid4().hex[:12]}",
                    slug=f"semantic-race-marker-{label}-{uuid.uuid4().hex[:12]}",
                    name="Synthetic transaction marker",
                    profile_name="semantic-race-test",
                    safe_metadata={},
                )
                session.add(marker)
                session.flush()
                source_payload = {
                    "purpose_summary": (
                        "Contenido incompatible de la segunda sesión."
                        if incompatible and label == "b"
                        else "Contenido concurrente compartido."
                    )
                }
                try:
                    proposal = SemanticProposalService(session).create_pending_proposal(
                        knowledge_version_id=version_id,
                        screen_knowledge_item_id=screen_id,
                        semantic_type=SemanticType.SCREEN_PURPOSE,
                        source_payload=source_payload,
                        evidence_payload={"screen_title": "Concurrent Screen"},
                        evidence_ids=["evidence:concurrent"],
                        generation_model="synthetic-concurrent-model",
                        prompt_version="screen-purpose-race-v1",
                        prompt_hash=HASH_B,
                        generation_parameters={"temperature": 0},
                    )
                    outcomes[label] = ("returned", proposal.semantic_id)
                except SemanticIdentityCollisionError:
                    if not incompatible or label != "b":
                        raise
                    outcomes[label] = ("collision", None)
                assert (
                    session.scalar(
                        sa.select(sa.func.count())
                        .select_from(ERPSystemRecord)
                        .where(ERPSystemRecord.id == marker.id)
                    )
                    == 1
                )
            if label == "a":
                winner_committed.set()
        except BaseException as exc:  # pragma: no cover - reported in parent thread
            failures.append(exc)
            if label == "a":
                winner_committed.set()
        finally:
            session.close()

    first = threading.Thread(target=worker, args=("a",), name="semantic-race-a")
    second = threading.Thread(target=worker, args=("b",), name="semantic-race-b")
    first.start()
    second.start()
    first.join(timeout=20)
    second.join(timeout=20)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []

    with Session(pg_engine) as verify:
        proposals = list(
            verify.scalars(
                sa.select(SemanticProposal).where(
                    SemanticProposal.screen_knowledge_item_id == screen_id
                )
            )
        )
        marker_count = verify.scalar(
            sa.select(sa.func.count())
            .select_from(ERPSystemRecord)
            .where(ERPSystemRecord.profile_name == "semantic-race-test")
        )
        action_count = verify.scalar(
            sa.select(sa.func.count())
            .select_from(SemanticReviewAction)
            .where(SemanticReviewAction.semantic_proposal_id == proposals[0].id)
        )
    assert len(proposals) == 1
    assert marker_count >= 2
    assert proposals[0].current_review_status == ReviewStatus.PENDING_REVIEW
    assert proposals[0].review_revision == 0
    assert action_count == 0
    assert soft_rollbacks["b"] >= 1
    return outcomes, proposals[0]


@pytest.mark.postgresql
def test_postgresql_concurrent_creation_uses_savepoint_and_keeps_session_usable(
    pg_engine, monkeypatch
):
    outcomes, proposal = run_creation_race(pg_engine, monkeypatch, incompatible=False)
    assert outcomes["a"] == ("returned", proposal.semantic_id)
    assert outcomes["b"] == ("returned", proposal.semantic_id)


@pytest.mark.postgresql
def test_postgresql_concurrent_incompatible_collision_is_domain_error(pg_engine, monkeypatch):
    outcomes, proposal = run_creation_race(pg_engine, monkeypatch, incompatible=True)
    assert outcomes["a"] == ("returned", proposal.semantic_id)
    assert outcomes["b"] == ("collision", None)
    assert proposal.source_payload == {"purpose_summary": "Contenido concurrente compartido."}


@pytest.mark.postgresql
def test_postgresql_service_creation_is_idempotent_and_validates_version(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as session, session.begin():
        version, screen = seed_screen(session)
        first = create_proposal(session, version, screen)
        second = create_proposal(session, version, screen)
        assert first.id == second.id
        assert first.source_payload == {"purpose_summary": "Permite una consulta sintética."}
        assert first.review_revision == 0
        other_version, _ = seed_screen(session)
        with pytest.raises(SemanticVersionMismatchError):
            SemanticProposalService(session).create_pending_proposal(
                knowledge_version_id=other_version.id,
                screen_knowledge_item_id=screen.id,
                semantic_type="screen_purpose",
                source_payload={"purpose_summary": "No debe persistirse."},
                evidence_payload={"screen_title": "Synthetic Screen"},
                evidence_ids=["evidence:test"],
                generation_model="synthetic-model",
                prompt_version="v1",
                prompt_hash=HASH_B,
                generation_parameters={},
            )
        count = session.scalar(
            sa.select(sa.func.count())
            .select_from(SemanticProposal)
            .where(SemanticProposal.screen_knowledge_item_id == screen.id)
        )
        assert count == 1


@pytest.mark.postgresql
def test_postgresql_review_flows_effective_history_and_source_immutability(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as session, session.begin():
        version, screen = seed_screen(session)
        approved = create_proposal(session, version, screen)
        corrected = create_proposal(session, version, screen, prompt_hash="c" * 64)
        rejected = create_proposal(session, version, screen, prompt_hash="d" * 64)
        originals = {
            proposal.id: dict(proposal.source_payload)
            for proposal in (approved, corrected, rejected)
        }
        service = SemanticReviewService(session)
        service.approve(
            approved.id,
            expected_revision=0,
            reviewer_subject="user:test",
            source="admin_api",
        )
        service.correct(
            corrected.id,
            {"purpose_summary": "Contenido corregido en PostgreSQL."},
            expected_revision=0,
            reviewer_subject="user:test",
            source="review_panel",
        )
        service.reject(
            rejected.id,
            expected_revision=0,
            reviewer_subject="user:test",
            source="cli",
        )
        assert service.publishable_payload(approved.id) == originals[approved.id]
        assert service.effective_payload(corrected.id) == {
            "purpose_summary": "Contenido corregido en PostgreSQL."
        }
        assert service.publishable_payload(rejected.id) is None
        service.reset_to_pending(
            corrected.id,
            expected_revision=1,
            reviewer_subject="user:test",
            source="migration",
        )
        assert service.effective_payload(corrected.id) == originals[corrected.id]
        assert len(service.history(corrected.id)) == 2
        assert all(
            proposal.source_payload == originals[proposal.id]
            for proposal in (approved, corrected, rejected)
        )


@pytest.mark.postgresql
def test_postgresql_stale_revision_and_failed_action_are_atomic(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as setup, setup.begin():
        version, screen = seed_screen(setup)
        proposal = create_proposal(setup, version, screen)
        proposal_id = proposal.id
        invalid_proposal = create_proposal(setup, version, screen, prompt_hash="e" * 64)
        invalid_proposal_id = invalid_proposal.id

    with Session(pg_engine) as first, first.begin():
        SemanticReviewService(first).approve(
            proposal_id,
            expected_revision=0,
            reviewer_subject="user:first",
            source="cli",
        )

    with Session(pg_engine) as stale:
        with pytest.raises(SemanticRevisionConflictError):
            with stale.begin():
                SemanticReviewService(stale).reject(
                    proposal_id,
                    expected_revision=0,
                    reviewer_subject="user:stale",
                    source="cli",
                )

    with Session(pg_engine) as invalid:
        with pytest.raises(SemanticPayloadError):
            with invalid.begin():
                proposal = invalid.get(SemanticProposal, invalid_proposal_id)
                SemanticReviewService(invalid).correct(
                    invalid_proposal_id,
                    proposal.source_payload,
                    expected_revision=0,
                    reviewer_subject="user:test",
                    source="cli",
                )

    with Session(pg_engine) as verify:
        proposal = verify.get(SemanticProposal, proposal_id)
        actions = list(
            verify.scalars(
                sa.select(SemanticReviewAction).where(
                    SemanticReviewAction.semantic_proposal_id == proposal_id
                )
            )
        )
        assert proposal.current_review_status == ReviewStatus.APPROVED
        assert proposal.review_revision == 1
        assert len(actions) == 1
        invalid_proposal = verify.get(SemanticProposal, invalid_proposal_id)
        invalid_actions = list(
            verify.scalars(
                sa.select(SemanticReviewAction).where(
                    SemanticReviewAction.semantic_proposal_id == invalid_proposal_id
                )
            )
        )
        assert invalid_proposal.current_review_status == ReviewStatus.PENDING_REVIEW
        assert invalid_proposal.review_revision == 0
        assert invalid_actions == []
