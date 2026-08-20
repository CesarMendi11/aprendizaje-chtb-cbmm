from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.analysis.generation.errors import InferenceGroundingError
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
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
)
from src.database.services.semantic_payloads import canonical_json_hash
from src.knowledge.canonical.enums import ReviewStatus
from src.pipeline.semantic_inference_job_executor import (
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
            schema_version="1.0",
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
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.calls = 0
        self.client = SimpleNamespace(settings=SimpleNamespace(model="llama3.2:3b"))

    def generate(self, _package):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.value


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
