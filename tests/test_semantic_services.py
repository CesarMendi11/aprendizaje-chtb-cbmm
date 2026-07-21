from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus, SemanticType
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from src.database.repositories import (
    SemanticProposalRepository,
    SemanticReviewActionRepository,
)
from src.database.services import (
    SemanticEffectivePayloadService,
    SemanticProposalService,
    SemanticReviewService,
)
from src.database.services.semantic_exceptions import (
    SemanticEntityTypeError,
    SemanticIdentityCollisionError,
    SemanticPayloadError,
    SemanticProposalNotFoundError,
    SemanticRevisionConflictError,
    SemanticScreenNotFoundError,
    SemanticScreenReviewError,
    SemanticSensitiveContentError,
    SemanticTransitionError,
    SemanticVersionMismatchError,
)
from src.database.services.semantic_payloads import canonical_json_hash
from src.knowledge.canonical.enums import ReviewStatus

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def seed_screen(
    session,
    *,
    status=ReviewStatus.APPROVED,
    entity_type="screen",
    suffix=None,
):
    suffix = suffix or uuid.uuid4().hex[:24].translate(str.maketrans("0123456789", "abcdefghij"))
    erp = ERPSystemRecord(
        id=f"erp:{suffix}",
        slug=f"erp-{suffix}",
        name="Synthetic ERP",
        profile_name="test",
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
    item = KnowledgeItem(
        knowledge_version=version,
        canonical_id=f"{entity_type}:{suffix}",
        entity_type=entity_type,
        title="Synthetic Screen",
        normalized_title="synthetic screen",
        route=f"/synthetic/{suffix}",
        content_hash=HASH_A,
        source_payload={"id": f"{entity_type}:{suffix}"},
        generated_review_status=status,
        current_review_status=status,
    )
    session.add(item)
    session.flush()
    return version, item


def create_proposal(session, version, screen, **overrides):
    values = {
        "knowledge_version_id": version.id,
        "screen_knowledge_item_id": screen.id,
        "semantic_type": SemanticType.SCREEN_PURPOSE,
        "source_payload": {"purpose_summary": "Permite consultar registros sintéticos."},
        "evidence_payload": {"screen_id": screen.canonical_id, "fields": ["Código"]},
        "evidence_ids": [" evidence:b ", "evidence:a", "evidence:b"],
        "generation_model": "synthetic-model",
        "prompt_version": "screen-purpose-v1",
        "prompt_hash": HASH_B,
        "generation_parameters": {"temperature": 0, "stream": False},
    }
    values.update(overrides)
    return SemanticProposalService(session).create_pending_proposal(**values)


def test_canonical_json_hash_is_deterministic_and_rejects_non_json():
    first = {"b": [True, None, 1, "x"], "a": {"z": 2}}
    second = {"a": {"z": 2}, "b": [True, None, 1, "x"]}
    assert canonical_json_hash(first) == canonical_json_hash(second)
    assert len(canonical_json_hash(first)) == 64
    with pytest.raises(SemanticPayloadError):
        canonical_json_hash({"bad": object()})


def test_creation_normalizes_evidence_and_is_idempotent(session):
    version, screen = seed_screen(session)
    first = create_proposal(session, version, screen)
    second = create_proposal(session, version, screen)
    assert first.id == second.id
    assert first.evidence_ids == ["evidence:a", "evidence:b"]
    assert first.semantic_id.startswith("semantic:") and len(first.semantic_id) == 73
    assert first.current_review_status == ReviewStatus.PENDING_REVIEW
    assert first.review_revision == 0
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1
    assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == 0


def test_creation_detaches_mutable_caller_payloads(session):
    version, screen = seed_screen(session)
    source = {"purpose_summary": "Contenido original.", "details": ["uno"]}
    evidence = {"fields": ["Código"]}
    evidence_ids = ["evidence:b", "evidence:a"]
    parameters = {"options": {"temperature": 0}}
    proposal = create_proposal(
        session,
        version,
        screen,
        source_payload=source,
        evidence_payload=evidence,
        evidence_ids=evidence_ids,
        generation_parameters=parameters,
    )
    source["details"].append("dos")
    evidence["fields"].append("Nombre")
    evidence_ids.append("evidence:c")
    parameters["options"]["temperature"] = 1
    assert proposal.source_payload["details"] == ["uno"]
    assert proposal.evidence_payload["fields"] == ["Código"]
    assert proposal.evidence_ids == ["evidence:a", "evidence:b"]
    assert proposal.generation_parameters == {"options": {"temperature": 0}}


def test_identity_changes_with_parameters_prompt_and_evidence(session):
    version, screen = seed_screen(session)
    base = create_proposal(session, version, screen)
    parameters = create_proposal(
        session, version, screen, generation_parameters={"temperature": 0.1}
    )
    prompt = create_proposal(session, version, screen, prompt_hash="c" * 64)
    evidence = create_proposal(session, version, screen, evidence_ids=["evidence:c"])
    assert (
        len({base.semantic_id, parameters.semantic_id, prompt.semantic_id, evidence.semantic_id})
        == 4
    )


def test_incompatible_existing_identity_is_rejected(session):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    session.execute(
        sa.update(SemanticProposal)
        .where(SemanticProposal.id == proposal.id)
        .values(source_payload={"purpose_summary": "Contenido incompatible"})
    )
    session.expire_all()
    with pytest.raises(SemanticIdentityCollisionError):
        create_proposal(session, version, screen)


def test_creation_validates_version_screen_and_review(session):
    version, screen = seed_screen(session)
    with pytest.raises(SemanticScreenNotFoundError):
        create_proposal(session, version, screen, screen_knowledge_item_id=uuid.uuid4())
    _, field = seed_screen(session, entity_type="field")
    with pytest.raises(SemanticEntityTypeError):
        create_proposal(session, field.knowledge_version, field)
    other_version, _ = seed_screen(session)
    with pytest.raises(SemanticVersionMismatchError):
        create_proposal(session, other_version, screen, knowledge_version_id=other_version.id)
    for status in (ReviewStatus.PENDING_REVIEW, ReviewStatus.REJECTED):
        blocked_version, blocked = seed_screen(session, status=status)
        with pytest.raises(SemanticScreenReviewError):
            create_proposal(session, blocked_version, blocked)


@pytest.mark.parametrize(
    "override, error",
    [
        ({"prompt_hash": "invalid"}, SemanticPayloadError),
        ({"semantic_type": "unknown"}, SemanticPayloadError),
        ({"generation_model": " "}, SemanticPayloadError),
        ({"source_payload": {}}, SemanticPayloadError),
        (
            {"source_payload": {"purpose_summary": "token=abcdefghijklmnopqrstuvwxyz1234567890"}},
            SemanticSensitiveContentError,
        ),
        ({"evidence_payload": {"password": "x"}}, SemanticSensitiveContentError),
        ({"evidence_ids": [""]}, SemanticPayloadError),
        ({"generation_parameters": {"token": "x"}}, SemanticSensitiveContentError),
    ],
)
def test_creation_rejects_invalid_or_sensitive_inputs(session, override, error):
    version, screen = seed_screen(session)
    with pytest.raises(error):
        create_proposal(session, version, screen, **override)


@pytest.mark.parametrize(
    "operation, expected",
    [
        ("approve", ReviewStatus.APPROVED),
        ("reject", ReviewStatus.REJECTED),
        ("correct", ReviewStatus.CORRECTED),
    ],
)
def test_valid_pending_transitions_increment_once(session, operation, expected):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    service = SemanticReviewService(session)
    kwargs = {
        "expected_revision": 0,
        "reviewer_subject": "user:synthetic",
        "source": "review_panel",
        "review_notes": "Synthetic review",
    }
    if operation == "correct":
        changed = service.correct(
            proposal.id,
            {"purpose_summary": "Descripción funcional corregida."},
            **kwargs,
        )
    else:
        changed = getattr(service, operation)(proposal.id, **kwargs)
    assert changed.current_review_status == expected
    assert changed.review_revision == 1
    history = service.history(proposal.id)
    assert len(history) == 1
    assert history[0]["reviewer_subject"] == "user:synthetic"
    assert history[0]["proposal_content_hash"] == (
        canonical_json_hash(history[0]["corrected_payload"])
        if operation == "correct"
        else proposal.source_content_hash
    )


@pytest.mark.parametrize("initial", ["approve", "reject", "correct"])
def test_reset_from_terminal_states_invalidates_prior_correction(session, initial):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    service = SemanticReviewService(session)
    common = {
        "reviewer_subject": "user:synthetic",
        "source": "cli",
    }
    if initial == "correct":
        service.correct(
            proposal.id,
            {"purpose_summary": "Contenido corregido."},
            expected_revision=0,
            **common,
        )
    else:
        getattr(service, initial)(proposal.id, expected_revision=0, **common)
    service.reset_to_pending(proposal.id, expected_revision=1, **common)
    assert proposal.current_review_status == ReviewStatus.PENDING_REVIEW
    assert proposal.review_revision == 2
    assert service.effective_payload(proposal.id) == proposal.source_payload
    assert service.publishable_payload(proposal.id) is None


def test_invalid_transitions_and_stale_revision_are_atomic(session):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    service = SemanticReviewService(session)
    common = {"reviewer_subject": "user:test", "source": "admin_api"}
    service.approve(proposal.id, expected_revision=0, **common)
    before_actions = len(service.history(proposal.id))
    for operation in ("approve", "reject"):
        with pytest.raises(SemanticTransitionError):
            getattr(service, operation)(proposal.id, expected_revision=1, **common)
    with pytest.raises(SemanticTransitionError):
        service.correct(
            proposal.id,
            {"purpose_summary": "Cambio inválido."},
            expected_revision=1,
            **common,
        )
    with pytest.raises(SemanticRevisionConflictError):
        service.reset_to_pending(proposal.id, expected_revision=0, **common)
    assert proposal.current_review_status == ReviewStatus.APPROVED
    assert proposal.review_revision == 1
    assert len(service.history(proposal.id)) == before_actions


def test_pending_reset_and_invalid_actor_source_are_rejected(session):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    service = SemanticReviewService(session)
    with pytest.raises(SemanticTransitionError):
        service.reset_to_pending(
            proposal.id,
            expected_revision=0,
            reviewer_subject="user:test",
            source="cli",
        )
    for reviewer, source in (("", "cli"), ("user:test", "unknown")):
        with pytest.raises(SemanticPayloadError):
            service.approve(
                proposal.id,
                expected_revision=0,
                reviewer_subject=reviewer,
                source=source,
            )


def test_review_actor_notes_are_normalized_and_invalid_uuid_is_domain_not_found(session):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    service = SemanticReviewService(session)
    service.approve(
        proposal.id,
        expected_revision=0,
        reviewer_subject="  user:synthetic   reviewer  ",
        source="cli",
        review_notes="  revisión   sintética  ",
    )
    action = service.history(proposal.id)[0]
    assert action["reviewer_subject"] == "user:synthetic reviewer"
    assert action["review_notes"] == "revisión sintética"
    with pytest.raises(SemanticProposalNotFoundError):
        service.history("not-a-uuid")


def test_correction_validation_effective_payload_and_original_immutability(session):
    version, screen = seed_screen(session)
    proposal = create_proposal(session, version, screen)
    original = dict(proposal.source_payload)
    service = SemanticReviewService(session)
    common = {
        "expected_revision": 0,
        "reviewer_subject": "user:test",
        "source": "migration",
    }
    with pytest.raises(SemanticPayloadError):
        service.correct(proposal.id, None, **common)
    with pytest.raises(SemanticPayloadError):
        service.correct(proposal.id, original, **common)
    corrected = {"purpose_summary": "Descripción humana revisada.", "scope": ["consulta"]}
    service.correct(proposal.id, corrected, **common)
    corrected["scope"].append("mutación posterior")
    assert proposal.source_payload == original
    expected = {"purpose_summary": "Descripción humana revisada.", "scope": ["consulta"]}
    assert service.effective_payload(proposal.id) == expected
    assert service.publishable_payload(proposal.id) == expected


def test_approved_rejected_and_public_descriptions_are_safe(session):
    version, screen = seed_screen(session)
    approved = create_proposal(session, version, screen)
    service = SemanticReviewService(session)
    service.approve(
        approved.id,
        expected_revision=0,
        reviewer_subject="user:test",
        source="admin_api",
        review_notes="Aprobación sintética",
    )
    assert service.effective_payload(approved.id) == approved.source_payload
    assert service.publishable_payload(approved.id) == approved.source_payload
    description = SemanticEffectivePayloadService(session).describe(approved.id)
    assert description["publishable"] is True
    assert description["history"][0]["source"] == "admin_api"
    assert "_sa_instance_state" not in str(description)

    rejected = create_proposal(session, version, screen, prompt_hash="e" * 64)
    service.reject(
        rejected.id,
        expected_revision=0,
        reviewer_subject="user:test",
        source="review_panel",
    )
    assert service.effective_payload(rejected.id) == rejected.source_payload
    assert service.publishable_payload(rejected.id) is None


def test_repositories_filter_and_order_history_stably(session):
    version, screen = seed_screen(session)
    first = create_proposal(session, version, screen)
    second = create_proposal(session, version, screen, prompt_hash="f" * 64)
    proposals = SemanticProposalRepository(session)
    assert [item.id for item in proposals.list_by_version(version.id)] == [
        first.id,
        second.id,
    ]
    assert [item.id for item in proposals.list_by_screen(screen.id)] == [first.id, second.id]
    assert [item.id for item in proposals.list_pending()] == [first.id, second.id]
    service = SemanticReviewService(session)
    service.approve(
        first.id,
        expected_revision=0,
        reviewer_subject="user:test",
        source="cli",
    )
    history = SemanticReviewActionRepository(session)
    assert history.latest_for_proposal(first.id).id == history.list_for_proposal(first.id)[0].id
    assert history.latest_reset(first.id) is None
