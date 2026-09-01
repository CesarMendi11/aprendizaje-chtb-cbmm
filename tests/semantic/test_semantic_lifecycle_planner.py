from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from erp_assistant.semantic.prompts import (
    GENERATION_PARAMETERS,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from erp_assistant.semantic.schemas import ControlEvidence, ModuleEvidence, ScreenEvidencePackage
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import ImportStatus, KnowledgeVersionStatus, SemanticType
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
    SemanticProposal,
)
from erp_assistant.semantic.services.semantic_lifecycle_planner import (
    SemanticLifecycleDecision,
    SemanticLifecyclePlanner,
)
from erp_assistant.semantic.services.semantic_payloads import (
    canonical_json_hash,
    semantic_evidence_compatibility_hash,
    semantic_evidence_compatibility_payload,
    validated_semantic_evidence_snapshot,
)
from erp_assistant.semantic.services.semantic_proposal_service import SemanticProposalService
from erp_assistant.semantic.services.semantic_review_service import SemanticReviewService
from erp_assistant.structural.canonical.enums import ReviewStatus

HASH = "a" * 64
MODEL = "llama3.2:3b"
SCREEN_ID = "screen:shared"


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


class MappingBuilder:
    def __init__(self, packages):
        self.packages = packages

    def build(self, version_id, screen_item_id):
        return self.packages[(uuid.UUID(str(version_id)), uuid.UUID(str(screen_item_id)))]


def seed_version(session, erp, *, name, status):
    run = ImportRun(
        erp=erp,
        source_knowledge_path=f"{name}.json",
        source_manifest_path=f"{name}.manifest.json",
        requested_knowledge_version=name,
        status=ImportStatus.SUCCEEDED,
        source_hashes={},
    )
    version = KnowledgeVersionRecord(
        erp=erp,
        import_run=run,
        schema_version="1.1.0",
        knowledge_version=name,
        canonical_hash=HASH,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=status,
    )
    screen = KnowledgeItem(
        knowledge_version=version,
        canonical_id=SCREEN_ID,
        entity_type="screen",
        title="Retenciones",
        normalized_title="retenciones",
        route="/retenciones",
        content_hash=HASH,
        source_payload={"id": SCREEN_ID, "title": "Retenciones"},
        generated_review_status=ReviewStatus.APPROVED,
        current_review_status=ReviewStatus.APPROVED,
    )
    session.add(screen)
    session.flush()
    return version, screen


def seed_replacement(session):
    suffix = uuid.uuid4().hex[:12]
    erp = ERPSystemRecord(
        id=f"erp:{suffix}",
        slug=f"erp-{suffix}",
        name="Synthetic ERP",
        profile_name="test",
        safe_metadata={},
    )
    source_version, source_screen = seed_version(
        session,
        erp,
        name=f"source-{suffix}",
        status=KnowledgeVersionStatus.ARCHIVED,
    )
    target_version, target_screen = seed_version(
        session,
        erp,
        name=f"target-{suffix}",
        status=KnowledgeVersionStatus.ACTIVE,
    )
    session.add(
        KnowledgeVersionPromotion(
            knowledge_version_id=target_version.id,
            previous_active_version_id=source_version.id,
            reviewer_subject="user:test",
            reason="Synthetic replacement",
            source="api",
            gate_snapshot={},
        )
    )
    session.flush()
    return source_version, source_screen, target_version, target_screen


def evidence(version, screen, **updates):
    values = {
        "erp_id": version.erp_id,
        "knowledge_version_id": version.id,
        "knowledge_version": version.knowledge_version,
        "screen_id": screen.canonical_id,
        "screen_title": "Retenciones",
        "screen_route": "/retenciones",
        "module": ModuleEvidence(module_id="module:test", name="Cuentas por cobrar"),
        "controls": [
            ControlEvidence(
                control_id="control:search",
                label="Buscar",
                control_type="button",
                mutative=False,
            )
        ],
        "main_content_text": "Módulo: Cuentas por cobrar\nPantalla: Retenciones",
        "primary_evidence_ids": ["evidence:screen"],
        "evidence_ids": ["evidence:screen"],
        "warnings": [],
    }
    values.update(updates)
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": HASH})
    digest = canonical_json_hash(provisional.model_dump(mode="json", exclude={"evidence_hash"}))
    return provisional.model_copy(update={"evidence_hash": digest})


def source_payload(screen_id=SCREEN_ID):
    return {
        "semantic_type": "screen_purpose",
        "screen_id": screen_id,
        "purpose_summary": "Permite consultar retenciones.",
        "supported_capabilities": [
            {
                "statement": "Permite buscar registros.",
                "evidence_refs": ["control:search"],
            }
        ],
        "limitations": [],
        "uncertainties": [],
    }


def publish_source(
    session,
    version,
    screen,
    package,
    *,
    model=MODEL,
    prompt_version=PROMPT_VERSION,
    prompt_hash=PROMPT_HASH,
    generation_parameters=None,
    corrected_payload=None,
):
    parameters = GENERATION_PARAMETERS if generation_parameters is None else generation_parameters
    proposal = SemanticProposalService(session).create_pending_proposal(
        knowledge_version_id=version.id,
        screen_knowledge_item_id=screen.id,
        semantic_type=SemanticType.SCREEN_PURPOSE,
        source_payload=source_payload(screen.canonical_id),
        evidence_payload=validated_semantic_evidence_snapshot(package),
        evidence_ids=list(package.evidence_ids),
        generation_model=model,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        generation_parameters=parameters,
    )
    review = SemanticReviewService(session)
    if corrected_payload is None:
        review.approve(
            proposal.id,
            expected_revision=0,
            reviewer_subject="user:test",
            source="review_panel",
            review_notes="Synthetic approval",
        )
    else:
        review.correct(
            proposal.id,
            corrected_payload,
            expected_revision=0,
            reviewer_subject="user:test",
            source="review_panel",
            review_notes="Synthetic correction",
        )
    return proposal


def planner(session, packages):
    return SemanticLifecyclePlanner(
        session,
        evidence_builder=MappingBuilder(packages),
    )


def test_compatibility_hash_excludes_only_knowledge_version_identity(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source = evidence(source_version, source_screen)
    target = evidence(target_version, target_screen)

    assert source.evidence_hash != target.evidence_hash
    assert semantic_evidence_compatibility_hash(source) == semantic_evidence_compatibility_hash(
        target
    )

    source_payload_value = semantic_evidence_compatibility_payload(source)
    target_payload_value = semantic_evidence_compatibility_payload(target)
    assert source_payload_value == target_payload_value
    assert "knowledge_version_id" not in source_payload_value
    assert "knowledge_version" not in source_payload_value
    assert "evidence_hash" not in source_payload_value

    changed = target.model_copy(
        update={"main_content_text": target.main_content_text + "\nDetalle adicional"}
    )
    changed_digest = canonical_json_hash(
        changed.model_dump(mode="json", exclude={"evidence_hash"})
    )
    changed = changed.model_copy(update={"evidence_hash": changed_digest})
    assert semantic_evidence_compatibility_hash(source) != semantic_evidence_compatibility_hash(
        changed
    )


def test_planner_generates_when_active_version_has_no_predecessor(session):
    suffix = uuid.uuid4().hex[:12]
    erp = ERPSystemRecord(
        id=f"erp:{suffix}",
        slug=f"erp-{suffix}",
        name="Synthetic ERP",
        profile_name="test",
        safe_metadata={},
    )
    version, screen = seed_version(
        session,
        erp,
        name=f"bootstrap-{suffix}",
        status=KnowledgeVersionStatus.ACTIVE,
    )
    package = evidence(version, screen)

    plan = planner(session, {(version.id, screen.id): package}).plan(
        version.id,
        screen.id,
        generation_model=MODEL,
    )

    assert plan.decision == SemanticLifecycleDecision.GENERATE
    assert plan.reasons == ("no_previous_active_version",)
    assert plan.source_semantic_proposal_id is None
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 0


def test_planner_generates_when_predecessor_has_no_publishable_semantic(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source_package = evidence(source_version, source_screen)
    target_package = evidence(target_version, target_screen)

    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)

    assert plan.decision == SemanticLifecycleDecision.GENERATE
    assert plan.reasons == ("no_publishable_source_semantic",)
    assert plan.source_knowledge_version_id == source_version.id
    assert plan.source_semantic_proposal_id is None


def test_planner_carries_forward_identical_safe_evidence_without_ollama(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source_package = evidence(source_version, source_screen)
    target_package = evidence(target_version, target_screen)
    corrected = source_payload()
    corrected["purpose_summary"] = "Permite consultar retenciones con redacción humana."
    source = publish_source(
        session,
        source_version,
        source_screen,
        source_package,
        corrected_payload=corrected,
    )

    count_before = session.scalar(select(func.count()).select_from(SemanticProposal))
    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)
    count_after = session.scalar(select(func.count()).select_from(SemanticProposal))

    assert plan.decision == SemanticLifecycleDecision.CARRY_FORWARD
    assert plan.reasons == ("semantic_evidence_and_generation_contract_compatible",)
    assert plan.source_semantic_proposal_id == source.id
    assert plan.source_knowledge_version_id == source_version.id
    assert plan.source_review_status == ReviewStatus.CORRECTED
    assert plan.source_review_revision == 1
    assert plan.source_effective_content_hash == canonical_json_hash(corrected)
    assert plan.source_effective_content_hash != canonical_json_hash(source_payload())
    assert plan.source_evidence_hash == source_package.evidence_hash
    assert plan.target_evidence_hash == target_package.evidence_hash
    assert source_package.evidence_hash != target_package.evidence_hash
    assert plan.source_compatibility_hash == plan.target_compatibility_hash
    assert count_before == count_after == 1


def test_planner_requires_reinference_when_semantic_evidence_changes(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source_package = evidence(source_version, source_screen)
    target_package = evidence(
        target_version,
        target_screen,
        main_content_text=(
            "Módulo: Cuentas por cobrar\nPantalla: Retenciones\nNuevo detalle funcional"
        ),
    )
    source = publish_source(session, source_version, source_screen, source_package)

    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)

    assert plan.decision == SemanticLifecycleDecision.REINFERENCE_REQUIRED
    assert plan.reasons == ("semantic_evidence_changed",)
    assert plan.source_semantic_proposal_id == source.id
    assert plan.source_compatibility_hash != plan.target_compatibility_hash


@pytest.mark.parametrize(
    ("source_overrides", "expected_reasons"),
    [
        ({"model": "old-model"}, ("generation_model_changed",)),
        ({"prompt_version": "old-prompt"}, ("prompt_version_changed",)),
        ({"prompt_hash": "b" * 64}, ("prompt_hash_changed",)),
        (
            {"generation_parameters": {"temperature": 0, "num_predict": 512}},
            ("generation_parameters_changed", "generation_parameters_hash_changed"),
        ),
    ],
)
def test_planner_requires_reinference_when_generation_contract_changes(
    session, source_overrides, expected_reasons
):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source_package = evidence(source_version, source_screen)
    target_package = evidence(target_version, target_screen)
    source = publish_source(
        session,
        source_version,
        source_screen,
        source_package,
        **source_overrides,
    )

    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)

    assert plan.decision == SemanticLifecycleDecision.REINFERENCE_REQUIRED
    assert plan.reasons == expected_reasons
    assert plan.source_semantic_proposal_id == source.id
    assert plan.source_compatibility_hash == plan.target_compatibility_hash


def test_planner_requires_reinference_when_publishable_source_is_stale(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    original_source_package = evidence(source_version, source_screen)
    current_source_package = evidence(
        source_version,
        source_screen,
        main_content_text=(
            "Módulo: Cuentas por cobrar\nPantalla: Retenciones\nSource changed after review"
        ),
    )
    target_package = evidence(target_version, target_screen)
    source = publish_source(session, source_version, source_screen, original_source_package)

    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): current_source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)

    assert plan.decision == SemanticLifecycleDecision.REINFERENCE_REQUIRED
    assert plan.reasons == ("source_semantic_stale",)
    assert plan.source_semantic_proposal_id == source.id


def test_planner_blocks_ambiguous_publishable_source_semantics(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source_package = evidence(source_version, source_screen)
    target_package = evidence(target_version, target_screen)
    publish_source(session, source_version, source_screen, source_package)
    publish_source(
        session,
        source_version,
        source_screen,
        source_package,
        prompt_version="other-prompt",
        prompt_hash="b" * 64,
    )

    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)

    assert plan.decision == SemanticLifecycleDecision.BLOCKED
    assert plan.reasons == ("ambiguous_publishable_source_semantics",)


def test_planner_blocks_ineligible_target_and_existing_target_semantic(session):
    source_version, source_screen, target_version, target_screen = seed_replacement(session)
    source_package = evidence(source_version, source_screen)
    publish_source(session, source_version, source_screen, source_package)

    ineligible = evidence(
        target_version,
        target_screen,
        controls=[],
        primary_evidence_ids=[],
    )
    plan = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): ineligible,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)
    assert plan.decision == SemanticLifecycleDecision.BLOCKED
    assert plan.reasons == (
        "target_ineligible:missing_primary_evidence",
        "target_ineligible:missing_functional_structure",
    )

    target_package = evidence(target_version, target_screen)
    publish_source(session, target_version, target_screen, target_package)
    blocked = planner(
        session,
        {
            (source_version.id, source_screen.id): source_package,
            (target_version.id, target_screen.id): target_package,
        },
    ).plan(target_version.id, target_screen.id, generation_model=MODEL)
    assert blocked.decision == SemanticLifecycleDecision.BLOCKED
    assert blocked.reasons == ("target_semantic_already_exists",)
