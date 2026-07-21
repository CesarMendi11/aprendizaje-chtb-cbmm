from __future__ import annotations

import inspect
import uuid
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.analysis.prompts import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from src.analysis.schemas import (
    ControlEvidence,
    GeneratedScreenPurposeCandidate,
    ModuleEvidence,
    ScreenEvidencePackage,
    ScreenPurposeInference,
)
from src.analysis.workflows import (
    ScreenPurposeProposalWorkflow,
    map_candidate_to_pending_proposal,
)
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
from src.database.services.semantic_exceptions import (
    SemanticCandidateMismatchError,
    SemanticEntityTypeError,
    SemanticIdentityCollisionError,
    SemanticPayloadError,
    SemanticScreenReviewError,
    SemanticVersionMismatchError,
    SemanticVersionNotActiveError,
)
from src.database.services.semantic_payloads import (
    ValidatedSemanticEvidenceSnapshot,
    canonical_json_hash,
    semantic_evidence_hash,
    validated_semantic_evidence_snapshot,
)
from src.database.services.semantic_proposal_service import SemanticProposalService
from src.database.services.semantic_review_service import SemanticReviewService
from src.knowledge.canonical.enums import ReviewStatus

HASH = "a" * 64


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def seed(session, *, status=ReviewStatus.APPROVED, entity_type="screen", active=True):
    suffix = uuid.uuid4().hex[:12]
    erp = ERPSystemRecord(
        id=f"erp:{suffix}",
        slug=f"erp-{suffix}",
        name="Synthetic ERP",
        profile_name="test",
        safe_metadata={},
    )
    run = ImportRun(
        erp=erp,
        source_knowledge_path="synthetic.json",
        source_manifest_path="manifest.json",
        requested_knowledge_version=suffix,
        status=ImportStatus.SUCCEEDED,
        source_hashes={},
    )
    version = KnowledgeVersionRecord(
        erp=erp,
        import_run=run,
        schema_version="1.0.0",
        knowledge_version=suffix,
        canonical_hash=HASH,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=KnowledgeVersionStatus.ACTIVE if active else KnowledgeVersionStatus.IMPORTED,
    )
    item = KnowledgeItem(
        knowledge_version=version,
        canonical_id=f"{entity_type}:{suffix}",
        entity_type=entity_type,
        title="Retenciones",
        normalized_title="retenciones",
        route="/retenciones",
        content_hash=HASH,
        source_payload={"id": f"{entity_type}:{suffix}", "title": "Retenciones"},
        generated_review_status=status,
        current_review_status=status,
    )
    session.add(item)
    session.flush()
    return version, item


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
        "evidence_ids": [],
        "warnings": [],
    }
    values.update(updates)
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": HASH})
    digest = canonical_json_hash(provisional.model_dump(mode="json", exclude={"evidence_hash"}))
    return provisional.model_copy(update={"evidence_hash": digest})


def candidate(package, **updates):
    inference = ScreenPurposeInference.model_validate(
        {
            "semantic_type": "screen_purpose",
            "screen_id": package.screen_id,
            "purpose_summary": "Permite consultar retenciones mediante búsqueda.",
            "supported_capabilities": [
                {"statement": "Permite buscar registros.", "evidence_refs": ["control:search"]}
            ],
            "limitations": [],
            "uncertainties": [],
        }
    )
    values = {
        "inference": inference,
        "generation_model": "llama3.2:3b",
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "generation_parameters": GENERATION_PARAMETERS,
        "generation_parameters_hash": GENERATION_PARAMETERS_HASH,
        "evidence_hash": package.evidence_hash,
        "evidence_ids": package.evidence_ids,
        "generated_content_hash": canonical_json_hash(inference.model_dump(mode="json")),
        "structured_output_mode": "json_schema",
        "warnings": package.warnings,
        "raw_response_hash": "b" * 64,
    }
    values.update(updates)
    return GeneratedScreenPurposeCandidate.model_validate(values)


class Builder:
    def __init__(self, package):
        self.package = package

    def build(self, *_):
        return self.package


class Inference:
    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.client = SimpleNamespace(settings=SimpleNamespace(model="llama3.2:3b"))

    def generate(self, _package):
        self.calls += 1
        return self.value


def workflow(session, version, screen, *, value=None, package=None):
    package = package or evidence(version, screen)
    inference = Inference(value or candidate(package))
    return (
        ScreenPurposeProposalWorkflow(
            session,
            evidence_builder=Builder(package),
            inference_service=inference,
        ),
        inference,
        package,
    )


def test_mapper_preserves_exact_functional_content_and_metadata(session):
    version, screen = seed(session)
    package = evidence(version, screen)
    generated = candidate(package)
    mapped = map_candidate_to_pending_proposal(
        package=package,
        candidate=generated,
        knowledge_version_id=version.id,
        screen_knowledge_item_id=screen.id,
        expected_model="llama3.2:3b",
    )
    assert mapped.semantic_type == SemanticType.SCREEN_PURPOSE
    assert mapped.source_payload == generated.inference.model_dump(mode="json")
    assert isinstance(mapped.evidence_payload, ValidatedSemanticEvidenceSnapshot)
    assert mapped.evidence_payload.payload["evidence_ids"] == []
    assert mapped.generation_model == generated.generation_model
    assert mapped.prompt_hash == generated.prompt_hash
    assert "raw_response" not in mapped.model_dump()
    assert "raw_response_hash" not in mapped.model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_hash", "c" * 64),
        ("evidence_ids", ["evidence:other"]),
        ("prompt_version", "other"),
        ("prompt_hash", "c" * 64),
        ("generation_parameters_hash", "c" * 64),
        ("generation_model", "other-model"),
        ("generated_content_hash", "c" * 64),
    ],
)
def test_mapper_rejects_candidate_metadata_mismatch(session, field, value):
    version, screen = seed(session)
    package = evidence(version, screen)
    generated = candidate(package).model_copy(update={field: value})
    with pytest.raises(SemanticCandidateMismatchError):
        map_candidate_to_pending_proposal(
            package=package,
            candidate=generated,
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            expected_model="llama3.2:3b",
        )


def test_mapper_rejects_other_screen_and_unknown_reference(session):
    version, screen = seed(session)
    package = evidence(version, screen)
    other = candidate(package).inference.model_copy(update={"screen_id": "screen:other"})
    with pytest.raises(SemanticCandidateMismatchError):
        map_candidate_to_pending_proposal(
            package=package,
            candidate=candidate(package).model_copy(update={"inference": other}),
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            expected_model="llama3.2:3b",
        )
    bad_claim = candidate(package).inference.supported_capabilities[0].model_copy(
        update={"evidence_refs": ["control:unknown"]}
    )
    bad_inference = candidate(package).inference.model_copy(
        update={"supported_capabilities": [bad_claim]}
    )
    with pytest.raises(SemanticCandidateMismatchError):
        map_candidate_to_pending_proposal(
            package=package,
            candidate=candidate(package).model_copy(update={"inference": bad_inference}),
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            expected_model="llama3.2:3b",
        )


def test_valid_candidate_persists_only_pending_proposal(session):
    version, screen = seed(session)
    service, inference, _ = workflow(session, version, screen)
    result = service.generate_and_persist(version.id, screen.id)
    assert result.status == ReviewStatus.PENDING_REVIEW
    assert result.semantic_type == SemanticType.SCREEN_PURPOSE
    assert result.created and result.ollama_called and not result.reused_existing
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1
    assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == 0
    assert inference.calls == 1


def test_existing_identity_skips_generation_and_reuses_row(session):
    version, screen = seed(session)
    service, inference, _ = workflow(session, version, screen)
    first = service.generate_and_persist(version.id, screen.id)
    second = service.generate_and_persist(version.id, screen.id)
    assert second.semantic_id == first.semantic_id
    assert second.reused_existing and not second.created and not second.ollama_called
    assert inference.calls == 1
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1


@pytest.mark.parametrize(
    ("status", "operation"),
    [
        (ReviewStatus.PENDING_REVIEW, None),
        (ReviewStatus.APPROVED, "approve"),
        (ReviewStatus.CORRECTED, "correct"),
        (ReviewStatus.REJECTED, "reject"),
    ],
)
def test_reviewed_proposal_is_reused_without_generation(session, status, operation):
    version, screen = seed(session)
    service, inference, _ = workflow(session, version, screen)
    first = service.generate_and_persist(version.id, screen.id)
    if operation:
        review = SemanticReviewService(session)
        kwargs = {
            "expected_revision": 0,
            "reviewer_subject": "user:synthetic",
            "source": "review_panel",
        }
        if operation == "correct":
            review.correct(
                first.proposal_id,
                {"purpose_summary": "Descripción funcional corregida."},
                **kwargs,
            )
        else:
            getattr(review, operation)(first.proposal_id, **kwargs)
    actions_before = session.scalar(select(func.count()).select_from(SemanticReviewAction))
    reused = service.generate_and_persist(version.id, screen.id)
    assert reused.semantic_id == first.semantic_id
    assert reused.status == status
    assert reused.reused_existing and not reused.created and not reused.ollama_called
    assert inference.calls == 1
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1
    assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == actions_before


def test_typed_snapshot_has_no_boolean_bypass_and_is_deeply_detached(session):
    assert "prevalidated_evidence" not in inspect.signature(
        SemanticProposalService.create_pending_proposal
    ).parameters
    version, screen = seed(session)
    package = evidence(version, screen)
    snapshot = validated_semantic_evidence_snapshot(package)
    original = snapshot.payload
    first_access = snapshot.payload
    first_access["controls"].append({"control_id": "tampered"})
    assert snapshot.payload == original
    assert snapshot.evidence_hash == package.evidence_hash
    assert snapshot.evidence_ids == package.evidence_ids
    package.controls.append(
        ControlEvidence(
            control_id="control:other",
            label="Otro",
            control_type="button",
            mutative=False,
        )
    )
    assert snapshot.payload == original
    with pytest.raises(FrozenInstanceError):
        snapshot._canonical_json = "{}"
    with pytest.raises(TypeError):
        SemanticProposalService(session).create_pending_proposal(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            source_payload={"purpose_summary": "Contenido sintético."},
            evidence_payload={"screen_id": str(screen.id)},
            evidence_ids=[],
            generation_model="model",
            prompt_version="prompt",
            prompt_hash="b" * 64,
            generation_parameters={},
            prevalidated_evidence=True,
        )


def test_snapshot_rejects_all_public_direct_construction():
    with pytest.raises(TypeError):
        ValidatedSemanticEvidenceSnapshot()
    with pytest.raises(TypeError):
        ValidatedSemanticEvidenceSnapshot("{}")
    with pytest.raises(TypeError):
        ValidatedSemanticEvidenceSnapshot(_canonical_json="{}")


def test_malformed_snapshot_becomes_sanitized_domain_error(session):
    version, screen = seed(session)
    malformed = object.__new__(ValidatedSemanticEvidenceSnapshot)
    with pytest.raises(SemanticPayloadError, match="snapshot de evidencia no es válido"):
        SemanticProposalService(session).create_pending_proposal(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            source_payload={"purpose_summary": "Contenido sintético."},
            evidence_payload=malformed,
            evidence_ids=[],
            generation_model="model",
            prompt_version="prompt",
            prompt_hash="b" * 64,
            generation_parameters={},
        )


def test_general_dictionary_with_evidence_ids_keeps_historical_identity():
    payload = {"screen_id": "screen:test", "evidence_ids": ["embedded:id"]}
    supplied_ids = ["embedded:id"]
    historical = canonical_json_hash(
        {"evidence_payload": payload, "evidence_ids": supplied_ids}
    )
    assert semantic_evidence_hash(payload, supplied_ids) == historical
    assert semantic_evidence_hash(payload, supplied_ids) != canonical_json_hash(payload)


@pytest.mark.parametrize("key", ["password", "token", "cookie", "authorization", "html"])
def test_snapshot_schema_rejects_sensitive_extra_keys(session, key):
    version, screen = seed(session)
    payload = evidence(version, screen).model_dump(mode="python")
    payload[key] = "forbidden"
    with pytest.raises(ValidationError):
        ScreenEvidencePackage.model_validate(payload)


@pytest.mark.parametrize("unsafe", ["<script>alert(1)</script>", "javascript:alert(1)"])
def test_snapshot_factory_rejects_executable_text(session, unsafe):
    version, screen = seed(session)
    package = evidence(version, screen).model_copy(update={"screen_title": unsafe})
    with pytest.raises(Exception) as captured:
        validated_semantic_evidence_snapshot(package)
    assert type(captured.value).__name__ == "SemanticSensitiveContentError"


def test_same_identity_with_different_content_is_a_collision(session):
    version, screen = seed(session)
    service, _, package = workflow(session, version, screen)
    original = candidate(package)
    service.persist_candidate(version.id, screen.id, original)
    changed_inference = original.inference.model_copy(
        update={"purpose_summary": "Permite buscar retenciones mediante criterios."}
    )
    changed = original.model_copy(
        update={
            "inference": changed_inference,
            "generated_content_hash": canonical_json_hash(
                changed_inference.model_dump(mode="json")
            ),
        }
    )
    with pytest.raises(SemanticIdentityCollisionError):
        service.persist_candidate(version.id, screen.id, changed)
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1


def test_generate_candidate_is_write_free(session):
    version, screen = seed(session)
    service, inference, _ = workflow(session, version, screen)
    before = session.scalar(select(func.count()).select_from(SemanticProposal))
    generated = service.generate_candidate(version.id, screen.id)
    assert generated.inference.screen_id == screen.canonical_id
    assert session.scalar(select(func.count()).select_from(SemanticProposal)) == before
    assert inference.calls == 1


@pytest.mark.parametrize(
    ("status", "entity_type", "active", "error"),
    [
        (ReviewStatus.PENDING_REVIEW, "screen", True, SemanticScreenReviewError),
        (ReviewStatus.REJECTED, "screen", True, SemanticScreenReviewError),
        (ReviewStatus.APPROVED, "field", True, SemanticEntityTypeError),
        (ReviewStatus.APPROVED, "screen", False, SemanticVersionNotActiveError),
    ],
)
def test_workflow_rejects_invalid_structural_context(session, status, entity_type, active, error):
    version, screen = seed(session, status=status, entity_type=entity_type, active=active)
    service, _, _ = workflow(session, version, screen)
    with pytest.raises(error):
        service.generate_and_persist(version.id, screen.id)


@pytest.mark.parametrize(
    ("update", "mismatch"),
    [
        ({"erp_id": "erp:other"}, "erp"),
        ({"knowledge_version_id": uuid.uuid4()}, "knowledge_version_id"),
        ({"knowledge_version": "other-version"}, "knowledge_version"),
        ({"screen_id": "screen:other"}, "screen"),
    ],
)
def test_workflow_rejects_cross_context_package_before_generation(
    session, update, mismatch
):
    version, screen = seed(session)
    package = evidence(version, screen).model_copy(update=update)
    service, inference, _ = workflow(session, version, screen, package=package)
    with pytest.raises(SemanticVersionMismatchError, match=mismatch):
        service.generate_and_persist(version.id, screen.id)
    assert inference.calls == 0
