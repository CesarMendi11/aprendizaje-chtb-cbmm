from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analysis.schemas import ScreenEvidencePackage
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus, SemanticType
from src.database.models import ERPSystemRecord, ImportRun, KnowledgeItem, KnowledgeVersionRecord
from src.database.services.semantic_chroma_sync_service import SemanticChromaSyncService
from src.database.services.semantic_payloads import (
    canonical_json_hash,
    validated_semantic_evidence_snapshot,
)
from src.database.services.semantic_proposal_service import SemanticProposalService
from src.database.services.semantic_review_service import SemanticReviewService
from src.knowledge.canonical.enums import ReviewStatus


class FakeEvidenceBuilder:
    def __init__(self, package):
        self.package = package

    def build(self, knowledge_version_id, screen_knowledge_item_id):
        return self.package


class FakeRepository:
    def __init__(self):
        self.documents = []
        self.embeddings = []
        self.sync_calls = 0

    def sync(self, documents, embeddings, *, erp_id, knowledge_version):
        self.sync_calls += 1
        self.documents = list(documents)
        self.embeddings = list(embeddings)
        return len(self.documents), 0


class FakeEmbeddings:
    model = "fake-semantic-embedding"

    def __init__(self, on_embed=None):
        self.dimensions = None
        self.on_embed = on_embed

    def embed(self, texts):
        values = list(texts)
        self.dimensions = 4
        if self.on_embed is not None:
            self.on_embed()
        return [[1.0, 0.0, 0.0, 0.0] for _ in values]


def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed(factory, *, approve=True, prompt_hash="b" * 64, purpose="Permite buscar registros."):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:semantic-sync",
            slug="semantic-sync",
            name="ERP Semantic Sync",
            profile_name="test",
            safe_metadata={},
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="knowledge.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="active-v1",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.0",
            knowledge_version="active-v1",
            canonical_hash="a" * 64,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        screen = KnowledgeItem(
            knowledge_version=version,
            canonical_id="screen:retenciones",
            entity_type="screen",
            title="Retenciones",
            normalized_title="retenciones",
            route="/admin/cuentasxcobrar/retenciones",
            content_hash="c" * 64,
            source_payload={"id": "screen:retenciones", "title": "Retenciones"},
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        session.add(screen)
        session.flush()

        raw = {
            "schema_version": "1.1",
            "erp_id": erp.id,
            "knowledge_version_id": str(version.id),
            "knowledge_version": version.knowledge_version,
            "screen_id": screen.canonical_id,
            "screen_title": screen.title,
            "screen_route": screen.route,
            "module": {"module_id": "module:cxp", "name": "Cuentas por cobrar"},
            "fields": [],
            "controls": [],
            "tables": [],
            "ui_states": [],
            "events": [],
            "transitions": [],
            "main_content_text": "Retenciones",
            "controls": [
                {
                    "control_id": "control:buscar",
                    "label": "Buscar",
                    "control_type": "button",
                    "mutative": False,
                }
            ],
            "primary_evidence_ids": ["evidence:screen"],
            "evidence_ids": ["evidence:screen"],
            "warnings": [],
        }
        provisional = ScreenEvidencePackage.model_validate(
            {**raw, "evidence_hash": "0" * 64}
        )
        digest = canonical_json_hash(
            provisional.model_dump(mode="json", exclude={"evidence_hash"})
        )
        package = provisional.model_copy(update={"evidence_hash": digest})
        source = {
            "semantic_type": "screen_purpose",
            "screen_id": screen.canonical_id,
            "purpose_summary": purpose,
            "supported_capabilities": [
                {"statement": "Permite buscar mediante los criterios disponibles.", "evidence_refs": []}
            ],
            "limitations": [],
            "uncertainties": [],
        }
        proposal = SemanticProposalService(session).create_pending_proposal(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            source_payload=source,
            evidence_payload=validated_semantic_evidence_snapshot(package),
            evidence_ids=["evidence:screen"],
            generation_model="llama3.2:3b",
            prompt_version="screen-purpose-v9",
            prompt_hash=prompt_hash,
            generation_parameters={"temperature": 0},
        )
        if approve:
            SemanticReviewService(session).approve(
                proposal.id,
                expected_revision=0,
                reviewer_subject="reviewer:test",
                source="admin_api",
                review_notes="Aprobada para prueba.",
            )
        session.flush()
        return str(version.id), erp.id, version.knowledge_version, str(screen.id), str(proposal.id), package


def test_prepare_projects_only_fresh_human_approved_semantics():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version, _screen_id, proposal_id, package = seed(factory)
    with factory() as session:
        service = SemanticChromaSyncService(session, evidence_builder=FakeEvidenceBuilder(package))
        version, documents, summary = service.prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        assert str(version.id) == version_id
        assert summary["publishable_proposals"] == 1
        assert summary["documents"] == 1
        assert summary["skipped"] == 0
        document = documents[0]
        assert "Propósito: Permite buscar registros." in document.text
        assert "Capacidad: Permite buscar mediante los criterios disponibles." in document.text
        assert document.metadata["canonical_id"] == "screen:retenciones"
        assert document.metadata["semantic_id"].startswith("semantic:")
        assert document.metadata["review_status"] == "approved"
        assert document.metadata["review_revision"] == 1
        assert document.metadata["document_kind"] == "semantic"
    engine.dispose()


def test_prepare_excludes_pending_and_stale_proposals():
    engine, factory = build_factory()
    _version_id, erp_id, knowledge_version, _screen_id, _proposal_id, package = seed(
        factory, approve=False
    )
    with factory() as session:
        service = SemanticChromaSyncService(session, evidence_builder=FakeEvidenceBuilder(package))
        _version, documents, summary = service.prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        assert documents == []
        assert summary["publishable_proposals"] == 0

    # Separate database: approved proposal but current evidence has a different hash.
    engine.dispose()
    engine, factory = build_factory()
    _version_id, erp_id, knowledge_version, _screen_id, _proposal_id, package = seed(factory)
    stale = package.model_copy(update={"evidence_hash": "f" * 64})
    with factory() as session:
        service = SemanticChromaSyncService(session, evidence_builder=FakeEvidenceBuilder(stale))
        _version, documents, summary = service.prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        assert documents == []
        assert summary["skipped_reasons"] == {"stale_evidence": 1}
    engine.dispose()


def test_run_embeds_and_syncs_dedicated_semantic_documents():
    engine, factory = build_factory()
    _version_id, erp_id, knowledge_version, _screen_id, _proposal_id, package = seed(factory)
    repository = FakeRepository()
    embeddings = FakeEmbeddings()
    with factory() as session:
        result = SemanticChromaSyncService(
            session,
            repository=repository,
            embeddings=embeddings,
            evidence_builder=FakeEvidenceBuilder(package),
        ).run(erp_id=erp_id, knowledge_version=knowledge_version)
        assert result.status == "succeeded"
        assert result.summary["documents"] == 1
        assert result.summary["inserted_or_updated"] == 1
        assert result.summary["embedding_model"] == "fake-semantic-embedding"
        assert result.summary["embedding_dimensions"] == 4
        assert len(repository.documents) == 1
        assert len(repository.embeddings) == 1
    engine.dispose()


def test_prepare_excludes_semantics_when_current_structure_is_ineligible():
    engine, factory = build_factory()
    _version_id, erp_id, knowledge_version, _screen_id, _proposal_id, package = seed(factory)
    ineligible = package.model_copy(update={"primary_evidence_ids": []})
    digest = canonical_json_hash(
        ineligible.model_dump(mode="json", exclude={"evidence_hash"})
    )
    ineligible = ineligible.model_copy(update={"evidence_hash": digest})
    with factory() as session:
        service = SemanticChromaSyncService(
            session, evidence_builder=FakeEvidenceBuilder(ineligible)
        )
        _version, documents, summary = service.prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        assert documents == []
        assert summary["skipped_reasons"] == {"current_evidence_ineligible": 1}
    engine.dispose()


def test_run_fails_closed_when_active_version_changes_after_embedding_before_sync():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version, _screen_id, _proposal_id, package = seed(factory)
    repository = FakeRepository()

    with factory() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(version_id))

        def archive_after_embedding():
            version.status = KnowledgeVersionStatus.ARCHIVED
            session.flush()

        embeddings = FakeEmbeddings(on_embed=archive_after_embedding)
        service = SemanticChromaSyncService(
            session,
            repository=repository,
            embeddings=embeddings,
            evidence_builder=FakeEvidenceBuilder(package),
        )

        with pytest.raises(ValueError, match="ACTIVE"):
            service.run(erp_id=erp_id, knowledge_version=knowledge_version)

        assert repository.sync_calls == 0

    engine.dispose()
