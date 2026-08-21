from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

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
from src.database.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    SemanticLifecycleOrigin,
    SemanticType,
)
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from src.database.services.semantic_chroma_sync_service import SemanticChromaSyncService
from src.database.services.semantic_effective_payload_service import (
    SemanticEffectivePayloadService,
)
from src.database.services.semantic_payloads import (
    canonical_json_hash,
    validated_semantic_evidence_snapshot,
)
from src.database.services.semantic_proposal_service import SemanticProposalService
from src.database.services.semantic_retrieval_authorization_service import (
    SemanticRetrievalAuthorizationService,
)
from src.database.services.semantic_review_service import SemanticReviewService
from src.knowledge.canonical.enums import ReviewStatus
from src.pipeline.semantic_inference_job_executor import SemanticInferenceJobExecutor
from src.vectorstore.semantic_chroma_repository import SemanticChromaRepository

HASH = "a" * 64
ERP_ID = "erp:semantic-lifecycle-e2e"
SOURCE_VERSION = "semantic-source-v1"
TARGET_VERSION = "semantic-target-v2"


class MappingEvidenceBuilder:
    def __init__(self, packages):
        self.packages = packages

    def build(self, version_id, screen_item_id):
        return self.packages[(uuid.UUID(str(version_id)), uuid.UUID(str(screen_item_id)))]


class FakeInference:
    def __init__(self, candidates):
        self.candidates = dict(candidates)
        self.calls = []
        self.client = SimpleNamespace(settings=SimpleNamespace(model="llama3.2:3b"))

    def generate(self, package):
        self.calls.append(package.screen_id)
        return self.candidates[package.screen_id]


class CountingInferenceFactory:
    def __init__(self, inference):
        self.inference = inference
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.inference


class FakeEmbeddings:
    model = "fake-semantic-embedding"
    dimensions = 4

    def embed(self, texts):
        values = list(texts)
        return [[1.0, 0.0, 0.0, 0.0] for _ in values]


class InMemoryCollection:
    def __init__(self):
        self.rows = {}

    @staticmethod
    def _matches(metadata, where):
        if where is None:
            return True
        if "$and" in where:
            return all(InMemoryCollection._matches(metadata, clause) for clause in where["$and"])
        return all(metadata.get(key) == value for key, value in where.items())

    def upsert(self, *, ids, documents, metadatas, embeddings):
        for document_id, document, metadata, embedding in zip(
            ids, documents, metadatas, embeddings, strict=True
        ):
            self.rows[document_id] = {
                "document": document,
                "metadata": dict(metadata),
                "embedding": list(embedding),
            }

    def get(self, *, where, include):
        selected = [
            (document_id, row)
            for document_id, row in sorted(self.rows.items())
            if self._matches(row["metadata"], where)
        ]
        result = {"ids": [document_id for document_id, _row in selected]}
        if "metadatas" in include:
            result["metadatas"] = [dict(row["metadata"]) for _document_id, row in selected]
        if "documents" in include:
            result["documents"] = [row["document"] for _document_id, row in selected]
        return result

    def delete(self, *, ids):
        for document_id in ids:
            self.rows.pop(document_id, None)

    def query(self, *, query_embeddings, n_results, where, include):
        del query_embeddings, include
        selected = [
            (document_id, row)
            for document_id, row in sorted(self.rows.items())
            if self._matches(row["metadata"], where)
        ][:n_results]
        return {
            "ids": [[document_id for document_id, _row in selected]],
            "metadatas": [[dict(row["metadata"]) for _document_id, row in selected]],
            "documents": [[row["document"] for _document_id, row in selected]],
            "distances": [[0.1 + index * 0.01 for index, _item in enumerate(selected)]],
        }


class InMemoryChromaClient:
    def __init__(self):
        self.collection = InMemoryCollection()

    def get_or_create_collection(self, name, metadata):
        assert name == "erp_assistant_semantic_v1"
        assert metadata == {"hnsw:space": "cosine"}
        return self.collection


def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def make_package(
    version_id,
    knowledge_version,
    *,
    screen_id,
    title,
    route,
    control_id,
    main_content_text,
):
    values = {
        "erp_id": ERP_ID,
        "knowledge_version_id": version_id,
        "knowledge_version": knowledge_version,
        "screen_id": screen_id,
        "screen_title": title,
        "screen_route": route,
        "module": ModuleEvidence(module_id="module:operations", name="Operaciones"),
        "controls": [
            ControlEvidence(
                control_id=control_id,
                label="Buscar",
                control_type="button",
                mutative=False,
            )
        ],
        "main_content_text": main_content_text,
        "primary_evidence_ids": [f"evidence:{screen_id}"],
        "evidence_ids": [f"evidence:{screen_id}"],
        "warnings": [],
    }
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": HASH})
    digest = canonical_json_hash(
        provisional.model_dump(mode="json", exclude={"evidence_hash"})
    )
    return provisional.model_copy(update={"evidence_hash": digest})


def semantic_payload(package, *, summary):
    control_id = package.controls[0].control_id
    return {
        "semantic_type": "screen_purpose",
        "screen_id": package.screen_id,
        "purpose_summary": summary,
        "supported_capabilities": [
            {
                "statement": "Permite buscar registros.",
                "evidence_refs": [control_id],
            }
        ],
        "limitations": [],
        "uncertainties": [],
    }


def generated_candidate(package, *, summary):
    inference = ScreenPurposeInference.model_validate(
        semantic_payload(package, summary=summary)
    )
    return GeneratedScreenPurposeCandidate.model_validate(
        {
            "inference": inference,
            "generation_model": "llama3.2:3b",
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": PROMPT_HASH,
            "generation_parameters": GENERATION_PARAMETERS,
            "generation_parameters_hash": GENERATION_PARAMETERS_HASH,
            "evidence_hash": package.evidence_hash,
            "evidence_ids": package.evidence_ids,
            "generated_content_hash": canonical_json_hash(
                inference.model_dump(mode="json")
            ),
            "structured_output_mode": "json_schema",
            "warnings": package.warnings,
            "raw_response_hash": "b" * 64,
        }
    )


def seed_versions(factory):
    specs = {
        "carry": {
            "canonical_id": "screen:carry",
            "title": "Consulta estable",
            "route": "/admin/operations/carry",
            "control_id": "control:carry:search",
        },
        "reinfer": {
            "canonical_id": "screen:reinfer",
            "title": "Consulta cambiante",
            "route": "/admin/operations/reinfer",
            "control_id": "control:reinfer:search",
        },
    }
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id=ERP_ID,
            slug="semantic-lifecycle-e2e",
            name="ERP Semantic Lifecycle E2E",
            profile_name="test",
            safe_metadata={},
        )
        source_run = ImportRun(
            erp=erp,
            source_knowledge_path="source.json",
            source_manifest_path="source.manifest.json",
            requested_knowledge_version=SOURCE_VERSION,
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        target_run = ImportRun(
            erp=erp,
            source_knowledge_path="target.json",
            source_manifest_path="target.manifest.json",
            requested_knowledge_version=TARGET_VERSION,
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        source = KnowledgeVersionRecord(
            erp=erp,
            import_run=source_run,
            schema_version="1.0",
            knowledge_version=SOURCE_VERSION,
            canonical_hash="1" * 64,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        target = KnowledgeVersionRecord(
            erp=erp,
            import_run=target_run,
            schema_version="1.0",
            knowledge_version=TARGET_VERSION,
            canonical_hash="2" * 64,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.IMPORTED,
        )
        session.add_all([source, target])
        session.flush()

        rows = {}
        for key, spec in specs.items():
            source_screen = KnowledgeItem(
                knowledge_version=source,
                canonical_id=spec["canonical_id"],
                entity_type="screen",
                title=spec["title"],
                normalized_title=spec["title"].lower(),
                route=spec["route"],
                content_hash=HASH,
                source_payload={"id": spec["canonical_id"], "title": spec["title"]},
                generated_review_status=ReviewStatus.APPROVED,
                current_review_status=ReviewStatus.APPROVED,
            )
            target_screen = KnowledgeItem(
                knowledge_version=target,
                canonical_id=spec["canonical_id"],
                entity_type="screen",
                title=spec["title"],
                normalized_title=spec["title"].lower(),
                route=spec["route"],
                content_hash=HASH,
                source_payload={"id": spec["canonical_id"], "title": spec["title"]},
                generated_review_status=ReviewStatus.APPROVED,
                current_review_status=ReviewStatus.APPROVED,
            )
            session.add_all([source_screen, target_screen])
            session.flush()
            rows[key] = {
                **spec,
                "source_screen_item_id": source_screen.id,
                "target_screen_item_id": target_screen.id,
            }

        return {
            "source_version_id": source.id,
            "target_version_id": target.id,
            "screens": rows,
        }


def build_packages(seeded):
    packages = {}
    for key, screen in seeded["screens"].items():
        source = make_package(
            seeded["source_version_id"],
            SOURCE_VERSION,
            screen_id=screen["canonical_id"],
            title=screen["title"],
            route=screen["route"],
            control_id=screen["control_id"],
            main_content_text=f"Pantalla: {screen['title']}\nSeñal funcional estable",
        )
        target_text = f"Pantalla: {screen['title']}\nSeñal funcional estable"
        if key == "reinfer":
            target_text += "\nNueva señal funcional de V2"
        target = make_package(
            seeded["target_version_id"],
            TARGET_VERSION,
            screen_id=screen["canonical_id"],
            title=screen["title"],
            route=screen["route"],
            control_id=screen["control_id"],
            main_content_text=target_text,
        )
        packages[(seeded["source_version_id"], screen["source_screen_item_id"])] = source
        packages[(seeded["target_version_id"], screen["target_screen_item_id"])] = target
    return packages


def create_source_semantics(factory, seeded, packages):
    ids = {}
    with factory.begin() as session:
        for key, screen in seeded["screens"].items():
            package = packages[(seeded["source_version_id"], screen["source_screen_item_id"])]
            proposal = SemanticProposalService(session).create_pending_proposal(
                knowledge_version_id=seeded["source_version_id"],
                screen_knowledge_item_id=screen["source_screen_item_id"],
                semantic_type=SemanticType.SCREEN_PURPOSE,
                source_payload=semantic_payload(
                    package,
                    summary=f"Propósito source para {screen['title']}.",
                ),
                evidence_payload=validated_semantic_evidence_snapshot(package),
                evidence_ids=list(package.evidence_ids),
                generation_model="llama3.2:3b",
                prompt_version=PROMPT_VERSION,
                prompt_hash=PROMPT_HASH,
                generation_parameters=GENERATION_PARAMETERS,
            )
            review = SemanticReviewService(session)
            if key == "carry":
                corrected = semantic_payload(
                    package,
                    summary="Propósito corregido por una persona y preservado por carry-forward.",
                )
                review.correct(
                    proposal.id,
                    corrected,
                    expected_revision=0,
                    reviewer_subject="user:source-reviewer",
                    source="review_panel",
                    review_notes="Corrección humana source para probar carry-forward.",
                )
            else:
                review.approve(
                    proposal.id,
                    expected_revision=0,
                    reviewer_subject="user:source-reviewer",
                    source="review_panel",
                    review_notes="Aprobación humana source para probar reinferencia.",
                )
            ids[key] = proposal.id
    return ids


def synthetic_promote(factory, seeded):
    # Promotion gating is certified separately. This integration test starts at the
    # post-promotion trust boundary and materializes only the immutable lineage/state
    # required by the semantic lifecycle planner.
    with factory.begin() as session:
        source = session.get(KnowledgeVersionRecord, seeded["source_version_id"])
        target = session.get(KnowledgeVersionRecord, seeded["target_version_id"])
        source.status = KnowledgeVersionStatus.ARCHIVED
        target.status = KnowledgeVersionStatus.ACTIVE
        session.add(
            KnowledgeVersionPromotion(
                knowledge_version_id=target.id,
                previous_active_version_id=source.id,
                reviewer_subject="user:promotion-reviewer",
                reason="Synthetic isolated lifecycle replacement",
                source="api",
                gate_snapshot={"integration_test": True},
            )
        )


def executor_params(seeded, key, package):
    screen = seeded["screens"][key]
    return {
        "active_only": True,
        "semantic_type": "screen_purpose",
        "knowledge_version_id": str(seeded["target_version_id"]),
        "knowledge_version": TARGET_VERSION,
        "erp_id": ERP_ID,
        "screen_knowledge_item_id": str(screen["target_screen_item_id"]),
        "screen_id": screen["canonical_id"],
        "screen_route": screen["route"],
        "evidence_hash": package.evidence_hash,
        "semantic_eligibility": "eligible",
    }


def physical_rows(repository, *, knowledge_version=None):
    where = {"erp_id": ERP_ID}
    if knowledge_version is not None:
        where = {"$and": [{"erp_id": ERP_ID}, {"knowledge_version": knowledge_version}]}
    result = repository.collection.get(where=where, include=["metadatas", "documents"])
    return list(zip(result["ids"], result.get("metadatas", []), result.get("documents", []), strict=True))


def test_cross_version_lifecycle_carry_forward_reinfer_chroma_and_reauthorization_e2e():
    engine, factory = build_factory()
    seeded = seed_versions(factory)
    packages = build_packages(seeded)
    source_proposal_ids = create_source_semantics(factory, seeded, packages)
    evidence_builder = MappingEvidenceBuilder(packages)

    client = InMemoryChromaClient()
    repository = SemanticChromaRepository(client=client)
    embeddings = FakeEmbeddings()

    # V1 is still ACTIVE here: materialize the exact source projection first so
    # replacement cleanup is exercised later rather than merely asserted in isolation.
    with factory() as session:
        source_sync = SemanticChromaSyncService(
            session,
            repository=repository,
            embeddings=embeddings,
            evidence_builder=evidence_builder,
        ).run(erp_id=ERP_ID, knowledge_version=SOURCE_VERSION)
        assert source_sync.summary["documents"] == 2
        assert source_sync.summary["removed_stale"] == 0
    assert len(physical_rows(repository, knowledge_version=SOURCE_VERSION)) == 2

    synthetic_promote(factory, seeded)

    carry_package = packages[
        (seeded["target_version_id"], seeded["screens"]["carry"]["target_screen_item_id"])
    ]
    reinfer_package = packages[
        (seeded["target_version_id"], seeded["screens"]["reinfer"]["target_screen_item_id"])
    ]
    inference = FakeInference(
        {
            "screen:reinfer": generated_candidate(
                reinfer_package,
                summary="Permite buscar información en Consulta cambiante.",
            )
        }
    )
    inference_factory = CountingInferenceFactory(inference)
    executor = SemanticInferenceJobExecutor(
        factory,
        inference_service_factory=inference_factory,
        evidence_builder_factory=lambda _session: evidence_builder,
        generation_model="llama3.2:3b",
    )

    carry_result = executor.execute(
        job_id="00000000-0000-0000-0000-000000000201",
        scope="screen",
        target=seeded["screens"]["carry"]["route"],
        parameters=executor_params(seeded, "carry", carry_package),
        progress=lambda *_: None,
    )
    assert carry_result["lifecycle_decision"] == "carry_forward"
    assert carry_result["lifecycle_origin"] == "carried_forward"
    assert carry_result["proposal_status"] == "corrected"
    assert carry_result["ollama_called"] is False
    assert inference_factory.calls == 0
    assert inference.calls == []

    reinfer_result = executor.execute(
        job_id="00000000-0000-0000-0000-000000000202",
        scope="screen",
        target=seeded["screens"]["reinfer"]["route"],
        parameters=executor_params(seeded, "reinfer", reinfer_package),
        progress=lambda *_: None,
    )
    assert reinfer_result["lifecycle_decision"] == "reinference_required"
    assert reinfer_result["lifecycle_origin"] == "reinferred"
    assert reinfer_result["proposal_status"] == "pending_review"
    assert reinfer_result["ollama_called"] is True
    assert inference_factory.calls == 1
    assert inference.calls == ["screen:reinfer"]

    with factory() as session:
        target_proposals = list(
            session.scalars(
                select(SemanticProposal)
                .where(SemanticProposal.knowledge_version_id == seeded["target_version_id"])
                .order_by(SemanticProposal.semantic_id)
            )
        )
        assert len(target_proposals) == 2
        by_screen = {
            proposal.screen_knowledge_item.canonical_id: proposal
            for proposal in target_proposals
        }
        carried = by_screen["screen:carry"]
        reinferred = by_screen["screen:reinfer"]
        assert carried.lifecycle_origin == SemanticLifecycleOrigin.CARRIED_FORWARD
        assert carried.source_semantic_proposal_id == source_proposal_ids["carry"]
        assert carried.source_review_status == ReviewStatus.CORRECTED
        assert carried.source_review_revision == 1
        assert carried.current_review_status == ReviewStatus.CORRECTED
        assert carried.review_revision == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(SemanticReviewAction)
                .where(SemanticReviewAction.semantic_proposal_id == carried.id)
            )
            == 0
        )
        assert (
            SemanticEffectivePayloadService(session)
            .publishable_payload(carried.id)["purpose_summary"]
            == "Propósito corregido por una persona y preservado por carry-forward."
        )
        assert reinferred.lifecycle_origin == SemanticLifecycleOrigin.REINFERRED
        assert reinferred.source_semantic_proposal_id == source_proposal_ids["reinfer"]
        assert reinferred.source_review_status == ReviewStatus.APPROVED
        assert reinferred.source_review_revision == 1
        assert reinferred.current_review_status == ReviewStatus.PENDING_REVIEW
        assert reinferred.review_revision == 0
        reinferred_id = reinferred.id

    # Before new HITL, only carried-forward truth is publishable. The first V2 sync
    # must simultaneously remove both archived V1 physical documents.
    with factory() as session:
        target_sync_before_hitl = SemanticChromaSyncService(
            session,
            repository=repository,
            embeddings=embeddings,
            evidence_builder=evidence_builder,
        ).run(erp_id=ERP_ID, knowledge_version=TARGET_VERSION)
        assert target_sync_before_hitl.summary["publishable_proposals"] == 1
        assert target_sync_before_hitl.summary["documents"] == 1
        assert target_sync_before_hitl.summary["removed_stale"] == 2

        target = session.get(KnowledgeVersionRecord, seeded["target_version_id"])
        carry_hits = repository.query(
            [1.0, 0.0, 0.0, 0.0],
            top_k=5,
            erp_id=ERP_ID,
            knowledge_version=TARGET_VERSION,
        )
        authorized = SemanticRetrievalAuthorizationService(
            session,
            evidence_builder=evidence_builder,
        ).authorize_hits(carry_hits, version=target)
        assert [row["screen_id"] for row in authorized] == ["screen:carry"]
        assert authorized[0]["review_status"] == "corrected"
        assert authorized[0]["review_revision"] == 0
        assert (
            authorized[0]["purpose_summary"]
            == "Propósito corregido por una persona y preservado por carry-forward."
        )

    assert physical_rows(repository, knowledge_version=SOURCE_VERSION) == []
    target_rows_before_hitl = physical_rows(repository, knowledge_version=TARGET_VERSION)
    assert len(target_rows_before_hitl) == 1
    assert target_rows_before_hitl[0][1]["screen_id"] == "screen:carry"

    # Reinference is not publishable until a fresh human decision exists in V2.
    with factory.begin() as session:
        SemanticReviewService(session).approve(
            reinferred_id,
            expected_revision=0,
            reviewer_subject="user:target-reviewer",
            source="review_panel",
            review_notes="Nueva evidencia V2 revisada por una persona.",
        )

    with factory() as session:
        target_sync_after_hitl = SemanticChromaSyncService(
            session,
            repository=repository,
            embeddings=embeddings,
            evidence_builder=evidence_builder,
        ).run(erp_id=ERP_ID, knowledge_version=TARGET_VERSION)
        assert target_sync_after_hitl.summary["publishable_proposals"] == 2
        assert target_sync_after_hitl.summary["documents"] == 2
        assert target_sync_after_hitl.summary["removed_stale"] == 0

        target = session.get(KnowledgeVersionRecord, seeded["target_version_id"])
        hits = repository.query(
            [1.0, 0.0, 0.0, 0.0],
            top_k=5,
            erp_id=ERP_ID,
            knowledge_version=TARGET_VERSION,
        )
        authorized = SemanticRetrievalAuthorizationService(
            session,
            evidence_builder=evidence_builder,
        ).authorize_hits(hits, version=target)
        assert {row["screen_id"] for row in authorized} == {
            "screen:carry",
            "screen:reinfer",
        }
        reinferred_hit = next(row for row in authorized if row["screen_id"] == "screen:reinfer")
        assert reinferred_hit["review_status"] == "approved"
        assert reinferred_hit["review_revision"] == 1
        assert (
            reinferred_hit["purpose_summary"]
            == "Permite buscar información en Consulta cambiante."
        )

    assert physical_rows(repository, knowledge_version=SOURCE_VERSION) == []
    final_target_rows = physical_rows(repository, knowledge_version=TARGET_VERSION)
    assert len(final_target_rows) == 2
    assert {metadata["screen_id"] for _id, metadata, _text in final_target_rows} == {
        "screen:carry",
        "screen:reinfer",
    }

    with factory() as session:
        reinferred = session.get(SemanticProposal, reinferred_id)
        assert reinferred.current_review_status == ReviewStatus.APPROVED
        assert reinferred.review_revision == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(SemanticReviewAction)
                .where(SemanticReviewAction.semantic_proposal_id == reinferred.id)
            )
            == 1
        )

    engine.dispose()
