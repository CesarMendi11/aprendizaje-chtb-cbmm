from __future__ import annotations

from datetime import datetime, timezone
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from erp_assistant.semantic.generation.errors import InferenceGroundingError
from erp_assistant.semantic.prompts import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from erp_assistant.semantic.schemas import (
    ControlEvidence,
    GeneratedScreenPurposeCandidate,
    ModuleEvidence,
    ScreenEvidencePackage,
    ScreenPurposeInference,
)
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    SemanticLifecycleOrigin,
    SemanticType,
)
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from erp_assistant.semantic.services.semantic_effective_payload_service import (
    SemanticEffectivePayloadService,
)
from erp_assistant.semantic.services.semantic_payloads import (
    canonical_json_hash,
    validated_semantic_evidence_snapshot,
)
from erp_assistant.semantic.services.semantic_proposal_service import SemanticProposalService
from erp_assistant.semantic.services.semantic_review_service import SemanticReviewService
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.orchestration.pipeline.executors.semantic_inference import (
    SemanticInferenceJobExecutionError,
    SemanticInferenceJobExecutor,
)

HASH = "a" * 64


def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed(factory, *, version_status=KnowledgeVersionStatus.ACTIVE, review=ReviewStatus.APPROVED):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:semantic-job",
            slug="semantic-job",
            name="ERP Semantic Job",
            profile_name="test",
            safe_metadata={},
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="knowledge.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="active-semantic-v1",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.1.0",
            knowledge_version="active-semantic-v1",
            canonical_hash=HASH,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=version_status,
        )
        screen = KnowledgeItem(
            knowledge_version=version,
            canonical_id="screen:retenciones",
            entity_type="screen",
            title="Retenciones",
            normalized_title="retenciones",
            route="/admin/cuentasxcobrar/retenciones",
            content_hash=HASH,
            source_payload={"id": "screen:retenciones", "title": "Retenciones"},
            generated_review_status=review,
            current_review_status=review,
        )
        session.add(screen)
        session.flush()
        return version.id, screen.id


def package(version_id, screen_item_id):
    values = {
        "erp_id": "erp:semantic-job",
        "knowledge_version_id": version_id,
        "knowledge_version": "active-semantic-v1",
        "screen_id": "screen:retenciones",
        "screen_title": "Retenciones",
        "screen_route": "/admin/cuentasxcobrar/retenciones",
        "module": ModuleEvidence(module_id="module:cxp", name="Cuentas por cobrar"),
        "controls": [
            ControlEvidence(
                control_id="control:buscar",
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
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": HASH})
    digest = canonical_json_hash(provisional.model_dump(mode="json", exclude={"evidence_hash"}))
    return provisional.model_copy(update={"evidence_hash": digest})


def candidate(value):
    inference = ScreenPurposeInference.model_validate(
        {
            "semantic_type": "screen_purpose",
            "screen_id": value.screen_id,
            "purpose_summary": "Permite consultar retenciones mediante búsqueda.",
            "supported_capabilities": [
                {
                    "statement": "Permite buscar registros.",
                    "evidence_refs": ["control:buscar"],
                }
            ],
            "limitations": [],
            "uncertainties": [],
        }
    )
    return GeneratedScreenPurposeCandidate.model_validate(
        {
            "inference": inference,
            "generation_model": "llama3.2:3b",
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": PROMPT_HASH,
            "generation_parameters": GENERATION_PARAMETERS,
            "generation_parameters_hash": GENERATION_PARAMETERS_HASH,
            "evidence_hash": value.evidence_hash,
            "evidence_ids": value.evidence_ids,
            "generated_content_hash": canonical_json_hash(inference.model_dump(mode="json")),
            "structured_output_mode": "json_schema",
            "warnings": value.warnings,
            "raw_response_hash": "b" * 64,
        }
    )


class Builder:
    def __init__(self, value):
        self.value = value

    def build(self, *_):
        return self.value


class Inference:
    def __init__(self, value=None, error=None, on_generate=None):
        self.value = value
        self.error = error
        self.on_generate = on_generate
        self.calls = 0
        self.client = SimpleNamespace(settings=SimpleNamespace(model="llama3.2:3b"))

    def generate(self, _package):
        self.calls += 1
        if self.on_generate is not None:
            self.on_generate()
        if self.error is not None:
            raise self.error
        return self.value


class CountingInferenceFactory:
    def __init__(self, inference):
        self.inference = inference
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.inference


class MappingBuilder:
    def __init__(self, packages):
        self.packages = packages

    def build(self, version_id, screen_item_id):
        return self.packages[(uuid.UUID(str(version_id)), uuid.UUID(str(screen_item_id)))]


def params(version_id, screen_id, evidence):
    return {
        "active_only": True,
        "semantic_type": "screen_purpose",
        "knowledge_version_id": str(version_id),
        "knowledge_version": "active-semantic-v1",
        "erp_id": "erp:semantic-job",
        "screen_knowledge_item_id": str(screen_id),
        "screen_id": "screen:retenciones",
        "screen_route": "/admin/cuentasxcobrar/retenciones",
        "evidence_hash": evidence.evidence_hash,
        "semantic_eligibility": "eligible",
    }




def seed_replacement(factory):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:semantic-lifecycle-job",
            slug="semantic-lifecycle-job",
            name="ERP Semantic Lifecycle Job",
            profile_name="test",
            safe_metadata={},
        )
        source_run = ImportRun(
            erp=erp,
            source_knowledge_path="source.json",
            source_manifest_path="source.manifest.json",
            requested_knowledge_version="source-semantic-v1",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        source_version = KnowledgeVersionRecord(
            erp=erp,
            import_run=source_run,
            schema_version="1.1.0",
            knowledge_version="source-semantic-v1",
            canonical_hash=HASH,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ARCHIVED,
        )
        source_screen = KnowledgeItem(
            knowledge_version=source_version,
            canonical_id="screen:retenciones",
            entity_type="screen",
            title="Retenciones",
            normalized_title="retenciones",
            route="/admin/cuentasxcobrar/retenciones",
            content_hash=HASH,
            source_payload={"id": "screen:retenciones", "title": "Retenciones"},
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        target_run = ImportRun(
            erp=erp,
            source_knowledge_path="target.json",
            source_manifest_path="target.manifest.json",
            requested_knowledge_version="target-semantic-v2",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        target_version = KnowledgeVersionRecord(
            erp=erp,
            import_run=target_run,
            schema_version="1.1.0",
            knowledge_version="target-semantic-v2",
            canonical_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        target_screen = KnowledgeItem(
            knowledge_version=target_version,
            canonical_id="screen:retenciones",
            entity_type="screen",
            title="Retenciones",
            normalized_title="retenciones",
            route="/admin/cuentasxcobrar/retenciones",
            content_hash=HASH,
            source_payload={"id": "screen:retenciones", "title": "Retenciones"},
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        session.add_all([source_screen, target_screen])
        session.flush()
        session.add(
            KnowledgeVersionPromotion(
                knowledge_version_id=target_version.id,
                previous_active_version_id=source_version.id,
                reviewer_subject="user:test",
                reason="Synthetic semantic lifecycle replacement",
                source="api",
                gate_snapshot={},
            )
        )
        session.flush()
        return {
            "source_version_id": source_version.id,
            "source_version": source_version.knowledge_version,
            "source_screen_id": source_screen.id,
            "target_version_id": target_version.id,
            "target_version": target_version.knowledge_version,
            "target_screen_id": target_screen.id,
        }


def lifecycle_package(version_id, knowledge_version, *, main_content_text=None):
    values = {
        "erp_id": "erp:semantic-lifecycle-job",
        "knowledge_version_id": version_id,
        "knowledge_version": knowledge_version,
        "screen_id": "screen:retenciones",
        "screen_title": "Retenciones",
        "screen_route": "/admin/cuentasxcobrar/retenciones",
        "module": ModuleEvidence(module_id="module:cxp", name="Cuentas por cobrar"),
        "controls": [
            ControlEvidence(
                control_id="control:buscar",
                label="Buscar",
                control_type="button",
                mutative=False,
            )
        ],
        "main_content_text": main_content_text
        or "Módulo: Cuentas por cobrar\nPantalla: Retenciones",
        "primary_evidence_ids": ["evidence:screen"],
        "evidence_ids": ["evidence:screen"],
        "warnings": [],
    }
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": HASH})
    digest = canonical_json_hash(
        provisional.model_dump(mode="json", exclude={"evidence_hash"})
    )
    return provisional.model_copy(update={"evidence_hash": digest})


def lifecycle_source_payload(*, summary="Permite consultar retenciones."):
    return {
        "semantic_type": "screen_purpose",
        "screen_id": "screen:retenciones",
        "purpose_summary": summary,
        "supported_capabilities": [
            {
                "statement": "Permite buscar registros.",
                "evidence_refs": ["control:buscar"],
            }
        ],
        "limitations": [],
        "uncertainties": [],
    }


def publish_lifecycle_source(
    factory,
    seeded,
    source_package,
    *,
    corrected_summary=None,
):
    with factory.begin() as session:
        proposal = SemanticProposalService(session).create_pending_proposal(
            knowledge_version_id=seeded["source_version_id"],
            screen_knowledge_item_id=seeded["source_screen_id"],
            semantic_type=SemanticType.SCREEN_PURPOSE,
            source_payload=lifecycle_source_payload(),
            evidence_payload=validated_semantic_evidence_snapshot(source_package),
            evidence_ids=list(source_package.evidence_ids),
            generation_model="llama3.2:3b",
            prompt_version=PROMPT_VERSION,
            prompt_hash=PROMPT_HASH,
            generation_parameters=GENERATION_PARAMETERS,
        )
        review = SemanticReviewService(session)
        if corrected_summary is None:
            review.approve(
                proposal.id,
                expected_revision=0,
                reviewer_subject="user:test",
                source="review_panel",
                review_notes="Synthetic lifecycle approval",
            )
        else:
            review.correct(
                proposal.id,
                lifecycle_source_payload(summary=corrected_summary),
                expected_revision=0,
                reviewer_subject="user:test",
                source="review_panel",
                review_notes="Synthetic lifecycle correction",
            )
        return proposal.id


def lifecycle_params(seeded, target_package):
    return {
        "active_only": True,
        "semantic_type": "screen_purpose",
        "knowledge_version_id": str(seeded["target_version_id"]),
        "knowledge_version": seeded["target_version"],
        "erp_id": "erp:semantic-lifecycle-job",
        "screen_knowledge_item_id": str(seeded["target_screen_id"]),
        "screen_id": "screen:retenciones",
        "screen_route": "/admin/cuentasxcobrar/retenciones",
        "evidence_hash": target_package.evidence_hash,
        "semantic_eligibility": "eligible",
    }


def test_executor_generates_and_persists_pending_proposal_without_self_approval():
    engine, factory = build_factory()
    version_id, screen_id = seed(factory)
    evidence = package(version_id, screen_id)
    inference = Inference(candidate(evidence))
    progress = []
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=lambda: inference,
        evidence_builder_factory=lambda _session: Builder(evidence),
    )

    result = executor.execute(
        job_id="00000000-0000-0000-0000-000000000001",
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        parameters=params(version_id, screen_id, evidence),
        progress=lambda stage, payload: progress.append((stage, payload)),
    )

    assert result["proposal_status"] == "pending_review"
    assert result["created"] is True
    assert result["reused_existing"] is False
    assert result["ollama_called"] is True
    assert result["purpose_summary"].startswith("Permite consultar")
    assert [stage for stage, _ in progress] == [
        "validating_active_screen",
        "evidence_prepared",
        "generating_semantic_proposal",
        "proposal_ready",
    ]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1
        proposal = session.scalar(select(SemanticProposal))
        assert proposal.current_review_status == ReviewStatus.PENDING_REVIEW
        assert proposal.review_revision == 0
    engine.dispose()


def test_executor_reuses_same_generation_identity_without_calling_ollama_twice():
    engine, factory = build_factory()
    version_id, screen_id = seed(factory)
    evidence = package(version_id, screen_id)
    inference = Inference(candidate(evidence))
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=lambda: inference,
        evidence_builder_factory=lambda _session: Builder(evidence),
    )
    kwargs = {
        "scope": "screen",
        "target": "/admin/cuentasxcobrar/retenciones",
        "parameters": params(version_id, screen_id, evidence),
        "progress": lambda *_: None,
    }
    first = executor.execute(job_id="00000000-0000-0000-0000-000000000001", **kwargs)
    second = executor.execute(job_id="00000000-0000-0000-0000-000000000002", **kwargs)

    assert first["semantic_id"] == second["semantic_id"]
    assert second["created"] is False
    assert second["reused_existing"] is True
    assert second["ollama_called"] is False
    assert inference.calls == 1
    engine.dispose()


def test_executor_rejects_grounding_failure_and_persists_no_proposal():
    engine, factory = build_factory()
    version_id, screen_id = seed(factory)
    evidence = package(version_id, screen_id)
    error = InferenceGroundingError(
        "Afirmación no respaldada",
        stage="grounding_validation",
        category="unsupported_view_detail_claim",
        location=("purpose_summary",),
    )
    inference = Inference(error=error)
    progress = []
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=lambda: inference,
        evidence_builder_factory=lambda _session: Builder(evidence),
    )

    with pytest.raises(SemanticInferenceJobExecutionError) as caught:
        executor.execute(
            job_id="00000000-0000-0000-0000-000000000003",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters=params(version_id, screen_id, evidence),
            progress=lambda stage, payload: progress.append((stage, payload)),
        )
    assert "unsupported_view_detail_claim" in str(caught.value)
    assert progress[-1][0] == "semantic_generation_rejected"
    assert progress[-1][1]["validation_stage"] == "grounding_validation"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 0
    engine.dispose()


def test_executor_fails_safe_when_captured_version_is_not_active():
    engine, factory = build_factory()
    version_id, screen_id = seed(factory, version_status=KnowledgeVersionStatus.IMPORTED)
    evidence = package(version_id, screen_id)
    inference = Inference(candidate(evidence))
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=lambda: inference,
        evidence_builder_factory=lambda _session: Builder(evidence),
    )

    with pytest.raises(SemanticInferenceJobExecutionError, match="dejó de ser ACTIVE"):
        executor.execute(
            job_id="00000000-0000-0000-0000-000000000004",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters=params(version_id, screen_id, evidence),
            progress=lambda *_: None,
        )
    assert inference.calls == 0
    engine.dispose()


def test_executor_rejects_ineligible_package_without_calling_ollama():
    engine, factory = build_factory()
    version_id, screen_id = seed(factory)
    evidence = package(version_id, screen_id).model_copy(
        update={"primary_evidence_ids": []}
    )
    digest = canonical_json_hash(
        evidence.model_dump(mode="json", exclude={"evidence_hash"})
    )
    evidence = evidence.model_copy(update={"evidence_hash": digest})
    inference = Inference(candidate(evidence))
    progress = []
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=lambda: inference,
        evidence_builder_factory=lambda _session: Builder(evidence),
    )

    with pytest.raises(SemanticInferenceJobExecutionError, match="missing_primary_evidence"):
        executor.execute(
            job_id="00000000-0000-0000-0000-000000000005",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters=params(version_id, screen_id, evidence),
            progress=lambda stage, payload: progress.append((stage, payload)),
        )

    assert inference.calls == 0
    assert progress[-1][0] == "semantic_eligibility_rejected"
    engine.dispose()


def test_executor_carries_forward_without_constructing_or_calling_ollama():
    engine, factory = build_factory()
    seeded = seed_replacement(factory)
    source_package = lifecycle_package(
        seeded["source_version_id"],
        seeded["source_version"],
    )
    target_package = lifecycle_package(
        seeded["target_version_id"],
        seeded["target_version"],
    )
    corrected_summary = "Permite consultar retenciones con redacción humana."
    source_proposal_id = publish_lifecycle_source(
        factory,
        seeded,
        source_package,
        corrected_summary=corrected_summary,
    )
    inference = Inference(candidate(target_package))
    inference_factory = CountingInferenceFactory(inference)
    packages = {
        (seeded["source_version_id"], seeded["source_screen_id"]): source_package,
        (seeded["target_version_id"], seeded["target_screen_id"]): target_package,
    }
    progress = []
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=inference_factory,
        evidence_builder_factory=lambda _session: MappingBuilder(packages),
        generation_model="llama3.2:3b",
    )

    result = executor.execute(
        job_id="00000000-0000-0000-0000-000000000101",
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        parameters=lifecycle_params(seeded, target_package),
        progress=lambda stage, payload: progress.append((stage, payload)),
    )

    assert inference_factory.calls == 0
    assert inference.calls == 0
    assert result["ollama_called"] is False
    assert result["created"] is True
    assert result["reused_existing"] is False
    assert result["lifecycle_origin"] == "carried_forward"
    assert result["lifecycle_decision"] == "carry_forward"
    assert result["proposal_status"] == "corrected"
    assert result["purpose_summary"] == corrected_summary
    assert result["source_semantic_proposal_id"] == str(source_proposal_id)
    assert [stage for stage, _ in progress] == [
        "validating_active_screen",
        "evidence_prepared",
        "carrying_forward_semantic_proposal",
        "proposal_ready",
    ]

    reused = executor.execute(
        job_id="00000000-0000-0000-0000-000000000104",
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        parameters=lifecycle_params(seeded, target_package),
        progress=lambda *_: None,
    )
    assert reused["semantic_id"] == result["semantic_id"]
    assert reused["created"] is False
    assert reused["reused_existing"] is True
    assert reused["ollama_called"] is False
    assert reused["lifecycle_origin"] == "carried_forward"
    assert inference_factory.calls == 0
    assert inference.calls == 0

    with factory() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(
                SemanticProposal.knowledge_version_id == seeded["target_version_id"]
            )
        )
        assert proposal is not None
        assert proposal.lifecycle_origin == SemanticLifecycleOrigin.CARRIED_FORWARD
        assert proposal.current_review_status == ReviewStatus.CORRECTED
        assert proposal.review_revision == 0
        assert proposal.source_semantic_proposal_id == source_proposal_id
        assert proposal.source_knowledge_version_id == seeded["source_version_id"]
        assert proposal.source_review_status == ReviewStatus.CORRECTED
        assert proposal.source_review_revision == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(SemanticReviewAction)
                .where(SemanticReviewAction.semantic_proposal_id == proposal.id)
            )
            == 0
        )
        assert (
            SemanticEffectivePayloadService(session)
            .publishable_payload(proposal.id)["purpose_summary"]
            == corrected_summary
        )
    engine.dispose()


def test_executor_reinfers_changed_evidence_and_requires_new_hitl():
    engine, factory = build_factory()
    seeded = seed_replacement(factory)
    source_package = lifecycle_package(
        seeded["source_version_id"],
        seeded["source_version"],
    )
    target_package = lifecycle_package(
        seeded["target_version_id"],
        seeded["target_version"],
        main_content_text=(
            "Módulo: Cuentas por cobrar\n"
            "Pantalla: Retenciones\n"
            "Nueva señal funcional"
        ),
    )
    source_proposal_id = publish_lifecycle_source(factory, seeded, source_package)
    inference = Inference(candidate(target_package))
    inference_factory = CountingInferenceFactory(inference)
    packages = {
        (seeded["source_version_id"], seeded["source_screen_id"]): source_package,
        (seeded["target_version_id"], seeded["target_screen_id"]): target_package,
    }
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=inference_factory,
        evidence_builder_factory=lambda _session: MappingBuilder(packages),
        generation_model="llama3.2:3b",
    )

    result = executor.execute(
        job_id="00000000-0000-0000-0000-000000000102",
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        parameters=lifecycle_params(seeded, target_package),
        progress=lambda *_: None,
    )

    assert inference_factory.calls == 1
    assert inference.calls == 1
    assert result["ollama_called"] is True
    assert result["lifecycle_origin"] == "reinferred"
    assert result["lifecycle_decision"] == "reinference_required"
    assert result["proposal_status"] == "pending_review"
    assert result["source_semantic_proposal_id"] == str(source_proposal_id)

    reused = executor.execute(
        job_id="00000000-0000-0000-0000-000000000105",
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        parameters=lifecycle_params(seeded, target_package),
        progress=lambda *_: None,
    )
    assert reused["semantic_id"] == result["semantic_id"]
    assert reused["created"] is False
    assert reused["reused_existing"] is True
    assert reused["ollama_called"] is False
    assert reused["lifecycle_origin"] == "reinferred"
    assert inference_factory.calls == 1
    assert inference.calls == 1

    with factory() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(
                SemanticProposal.knowledge_version_id == seeded["target_version_id"]
            )
        )
        assert proposal is not None
        assert proposal.lifecycle_origin == SemanticLifecycleOrigin.REINFERRED
        assert proposal.current_review_status == ReviewStatus.PENDING_REVIEW
        assert proposal.review_revision == 0
        assert proposal.source_semantic_proposal_id == source_proposal_id
        assert proposal.source_review_status == ReviewStatus.APPROVED
        assert proposal.source_review_revision == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(SemanticReviewAction)
                .where(SemanticReviewAction.semantic_proposal_id == proposal.id)
            )
            == 0
        )
    engine.dispose()


def test_executor_fails_closed_when_lifecycle_plan_changes_during_generation():
    engine, factory = build_factory()
    seeded = seed_replacement(factory)
    source_package = lifecycle_package(
        seeded["source_version_id"],
        seeded["source_version"],
    )
    target_package = lifecycle_package(
        seeded["target_version_id"],
        seeded["target_version"],
        main_content_text=(
            "Módulo: Cuentas por cobrar\n"
            "Pantalla: Retenciones\n"
            "Nueva señal funcional"
        ),
    )
    source_proposal_id = publish_lifecycle_source(factory, seeded, source_package)

    def reset_source_to_pending():
        with factory.begin() as session:
            SemanticReviewService(session).reset_to_pending(
                source_proposal_id,
                expected_revision=1,
                reviewer_subject="user:test",
                source="review_panel",
                review_notes="Concurrent lifecycle change",
            )

    inference = Inference(
        candidate(target_package),
        on_generate=reset_source_to_pending,
    )
    packages = {
        (seeded["source_version_id"], seeded["source_screen_id"]): source_package,
        (seeded["target_version_id"], seeded["target_screen_id"]): target_package,
    }
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=lambda: inference,
        evidence_builder_factory=lambda _session: MappingBuilder(packages),
        generation_model="llama3.2:3b",
    )

    with pytest.raises(
        SemanticInferenceJobExecutionError,
        match="decisión lifecycle semántica cambió",
    ):
        executor.execute(
            job_id="00000000-0000-0000-0000-000000000103",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters=lifecycle_params(seeded, target_package),
            progress=lambda *_: None,
        )

    assert inference.calls == 1
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SemanticProposal)
                .where(SemanticProposal.knowledge_version_id == seeded["target_version_id"])
            )
            == 0
        )
        source = session.get(SemanticProposal, source_proposal_id)
        assert source.current_review_status == ReviewStatus.PENDING_REVIEW
        assert source.review_revision == 2
    engine.dispose()
