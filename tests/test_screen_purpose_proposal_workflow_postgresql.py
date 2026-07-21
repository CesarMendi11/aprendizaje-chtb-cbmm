from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
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
from src.analysis.workflows import ScreenPurposeProposalWorkflow
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from src.database.services.semantic_payloads import canonical_json_hash
from src.database.services.semantic_review_service import SemanticReviewService
from src.knowledge.canonical.enums import ReviewStatus

HASH = "a" * 64


def _test_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    database = urlsplit(url).path.lstrip("/").casefold()
    if not _safe_database_name(database):
        pytest.fail("TEST_DATABASE_URL no apunta a una base temporal segura")
    return url


def _safe_database_name(database: str) -> bool:
    return "semantic_test" in database.casefold()


@pytest.mark.parametrize(
    ("database", "expected"),
    [
        ("erp_assistant_test_random", False),
        ("erp_assistant_semantic_test_phase5", True),
        ("erp_assistant", False),
    ],
)
def test_safe_database_name_requires_semantic_marker(database, expected):
    assert _safe_database_name(database) is expected


@pytest.fixture(scope="module")
def pg_engine():
    engine = sa.create_engine(_test_url(), pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        pytest.fail("La prueba requiere PostgreSQL")
    yield engine
    engine.dispose()


def _context(session):
    suffix = uuid.uuid4().hex[:16]
    erp = ERPSystemRecord(
        id=f"erp:test:{suffix}",
        slug=f"test-{suffix}",
        name="Synthetic ERP",
        profile_name="phase-5-test",
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
        status=KnowledgeVersionStatus.ACTIVE,
    )
    screen = KnowledgeItem(
        knowledge_version=version,
        canonical_id=f"screen:test:{suffix}",
        entity_type="screen",
        title="Synthetic Retenciones",
        normalized_title="synthetic retenciones",
        route=f"/test/{suffix}",
        content_hash=HASH,
        source_payload={"id": f"screen:test:{suffix}"},
        generated_review_status=ReviewStatus.APPROVED,
        current_review_status=ReviewStatus.APPROVED,
    )
    session.add(screen)
    session.flush()
    values = {
        "erp_id": erp.id,
        "knowledge_version_id": version.id,
        "knowledge_version": version.knowledge_version,
        "screen_id": screen.canonical_id,
        "screen_title": "Synthetic Retenciones",
        "screen_route": screen.route,
        "module": ModuleEvidence(module_id="module:test", name="Synthetic Module"),
        "controls": [
            ControlEvidence(
                control_id="control:search",
                label="Buscar",
                control_type="button",
                mutative=False,
            )
        ],
        "main_content_text": "Pantalla: Synthetic Retenciones",
        "evidence_ids": [],
        "warnings": [],
    }
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": HASH})
    evidence_hash = canonical_json_hash(
        provisional.model_dump(mode="json", exclude={"evidence_hash"})
    )
    package = provisional.model_copy(update={"evidence_hash": evidence_hash})
    inference = ScreenPurposeInference.model_validate(
        {
            "semantic_type": "screen_purpose",
            "screen_id": screen.canonical_id,
            "purpose_summary": "Permite consultar retenciones sintéticas.",
            "supported_capabilities": [
                {"statement": "Permite buscar registros.", "evidence_refs": ["control:search"]}
            ],
            "limitations": [],
            "uncertainties": [],
        }
    )
    candidate = GeneratedScreenPurposeCandidate(
        inference=inference,
        generation_model="llama3.2:3b",
        prompt_version=PROMPT_VERSION,
        prompt_hash=PROMPT_HASH,
        generation_parameters=GENERATION_PARAMETERS,
        generation_parameters_hash=GENERATION_PARAMETERS_HASH,
        evidence_hash=package.evidence_hash,
        evidence_ids=[],
        generated_content_hash=canonical_json_hash(inference.model_dump(mode="json")),
        structured_output_mode="json_schema",
        warnings=[],
    )
    return version, screen, package, candidate


class Builder:
    def __init__(self, package):
        self.package = package

    def build(self, *_):
        return self.package


class Inference:
    def __init__(self, candidate):
        self.candidate = candidate
        self.calls = 0
        self.client = SimpleNamespace(settings=SimpleNamespace(model="llama3.2:3b"))

    def generate(self, _):
        self.calls += 1
        return self.candidate


@pytest.mark.postgresql
def test_postgresql_pending_idempotency_and_external_rollback(pg_engine):
    semantic_id = None
    with Session(pg_engine, expire_on_commit=False) as session:
        transaction = session.begin()
        version, screen, package, candidate = _context(session)
        inference = Inference(candidate)
        workflow = ScreenPurposeProposalWorkflow(
            session,
            evidence_builder=Builder(package),
            inference_service=inference,
        )
        first = workflow.generate_and_persist(version.id, screen.id)
        second = workflow.generate_and_persist(version.id, screen.id)
        semantic_id = first.semantic_id
        assert first.status == ReviewStatus.PENDING_REVIEW
        assert second.semantic_id == semantic_id
        assert second.reused_existing and not second.ollama_called
        assert inference.calls == 1
        assert session.scalar(select(func.count()).select_from(SemanticProposal)) == 1
        assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == 0
        SemanticReviewService(session).approve(
            first.proposal_id,
            expected_revision=0,
            reviewer_subject="user:postgresql-test",
            source="review_panel",
        )
        action_count = session.scalar(select(func.count()).select_from(SemanticReviewAction))
        reviewed = workflow.generate_and_persist(version.id, screen.id)
        assert reviewed.status == ReviewStatus.APPROVED
        assert reviewed.semantic_id == semantic_id
        assert reviewed.reused_existing and not reviewed.ollama_called
        assert inference.calls == 1
        final_action_count = session.scalar(
            select(func.count()).select_from(SemanticReviewAction)
        )
        assert final_action_count == action_count
        transaction.rollback()
    with Session(pg_engine) as verification:
        assert verification.scalar(
            select(func.count()).select_from(SemanticProposal).where(
                SemanticProposal.semantic_id == semantic_id
            )
        ) == 0
