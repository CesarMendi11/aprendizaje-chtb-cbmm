from __future__ import annotations

import json

import chromadb
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import erp_assistant.persistence.postgres.models  # noqa: F401
from erp_assistant.config.ollama_settings import OllamaEmbeddingSettings
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus, SyncStatus, SyncTarget
from erp_assistant.persistence.postgres.models import KnowledgeItem, KnowledgeVersionRecord, SyncJob
from erp_assistant.structural.services.canonical_import_service import CanonicalImportService
from erp_assistant.projections.chroma.structural_sync_service import (
    ChromaSyncService,
    SafeDocumentBuilder,
)
from erp_assistant.structural.services.knowledge_review_service import KnowledgeReviewService
from erp_assistant.structural.canonical.builder import CanonicalKnowledgeBuilder
from erp_assistant.structural.canonical.exporter import CanonicalKnowledgeExporter
from erp_assistant.projections.chroma.structural_repository import ChromaRepository
from erp_assistant.integrations.ollama.embeddings import OllamaEmbeddingClient, OllamaEmbeddingError
from erp_assistant.projections.chroma.structural_repository import collection_name, document_id
from tests.fixtures.canonical import fictional_artifacts, fictional_profile


class FakeEmbeddings:
    model = "fake-embedding"
    dimensions = None

    def embed(self, texts):
        values = [texts] if isinstance(texts, str) else texts
        self.dimensions = 3
        return [[float(len(text) % 7), 1.0, 0.5] for text in values]


@pytest.fixture
def chroma_session(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    builder = CanonicalKnowledgeBuilder()
    knowledge = builder.build(fictional_profile(), fictional_artifacts())
    CanonicalKnowledgeExporter().export(
        knowledge, tmp_path, build_report=builder.build_report(knowledge)
    )
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            CanonicalImportService(session).import_canonical(
                tmp_path / "knowledge.json", tmp_path / "manifest.json"
            )
        yield session


def _approve_correct_reject(session):
    erp = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "erp_system"))
    screen = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "screen"))
    field = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "field"))
    pending = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "control"))
    review = KnowledgeReviewService(session)
    session.rollback()
    with session.begin():
        review.approve(erp.id)
        corrected = {
            k: v
            for k, v in screen.source_payload.items()
            if k not in {"review_status", "reviewed_at", "reviewed_by", "review_notes"}
        }
        corrected["description"] = "Descripción corregida segura"
        review.correct(screen.id, corrected, notes="prueba")
        review.reject(field.id, notes="prueba")
    return erp, screen, field, pending


def test_prepare_only_approved_corrected_effective_and_safe(chroma_session):
    erp, screen, rejected, pending = _approve_correct_reject(chroma_session)
    before = {
        item.id: (str(item.current_review_status), item.review_revision)
        for item in chroma_session.scalars(select(KnowledgeItem))
    }
    version, documents, summary = ChromaSyncService(chroma_session).prepare()
    after = {
        item.id: (str(item.current_review_status), item.review_revision)
        for item in chroma_session.scalars(select(KnowledgeItem))
    }
    assert summary["eligible_items"] == summary["documents"] == 2
    assert {d.metadata["review_status"] for d in documents} == {"approved", "corrected"}
    assert rejected.canonical_id not in {d.metadata["canonical_id"] for d in documents}
    assert pending.canonical_id not in {d.metadata["canonical_id"] for d in documents}
    corrected_doc = next(d for d in documents if d.metadata["canonical_id"] == screen.canonical_id)
    assert "Descripción corregida segura" in corrected_doc.text
    assert "ERP:" in corrected_doc.text and "Ruta:" in corrected_doc.text
    forbidden = {
        "selector",
        "source_payload",
        "cookie",
        "token",
        "fingerprint",
        "row_count_observed",
    }
    assert not forbidden.intersection(corrected_doc.metadata)
    assert not any(
        word in corrected_doc.text.casefold() for word in ("selector:", "cookie:", "token:")
    )
    assert before == after and version.erp_id == erp.canonical_id


def test_ids_collection_and_synthetic_erp_are_deterministic(chroma_session):
    erp, *_ = _approve_correct_reject(chroma_session)
    version, docs_a, _ = ChromaSyncService(chroma_session).prepare()
    _, docs_b, _ = ChromaSyncService(chroma_session).prepare()
    assert [d.id for d in docs_a] == [d.id for d in docs_b]
    assert docs_a[0].id == document_id(
        erp.canonical_id, version.knowledge_version, docs_a[0].metadata["canonical_id"]
    )
    assert collection_name() == "erp_assistant_knowledge_v1"
    assert "Northwind Operations" in docs_a[0].text


def test_safe_document_builder_distinguishes_deliberate_evidence_skip_from_missing_label():
    builder = SafeDocumentBuilder()
    erp = type("ERP", (), {"id": "erp:one", "name": "ERP Uno"})()
    entries = [
        {
            "canonical_id": "evidence:a",
            "entity_type": "evidence",
            "parent_canonical_id": "screen:a",
            "route": None,
            "content_hash": "a" * 64,
            "review_status": "approved",
            "payload": {
                "id": "evidence:a",
                "evidence_type": "network",
                "source_entity_type": "screen",
            },
        },
        {
            "canonical_id": "table:a",
            "entity_type": "table",
            "parent_canonical_id": "screen:a",
            "route": None,
            "content_hash": "b" * 64,
            "review_status": "approved",
            "payload": {"id": "table:a", "name": ""},
        },
    ]

    documents, reasons, by_type, details = builder.build(
        entries, erp=erp, knowledge_version="v1"
    )

    assert documents == []
    assert reasons == {
        "missing_safe_label": 1,
        "not_projected_by_design": 1,
    }
    assert by_type == {"evidence": 1, "table": 1}
    assert details == {
        "evidence": {"not_projected_by_design": 1},
        "table": {"missing_safe_label": 1},
    }


def test_chroma_upsert_idempotent_stale_scope_and_search_order(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    repo = ChromaRepository(client=client)
    builder = SafeDocumentBuilder()
    erp = type("ERP", (), {"id": "erp:one", "name": "ERP Uno"})()
    entries = [
        {
            "canonical_id": "screen:a",
            "entity_type": "screen",
            "parent_canonical_id": None,
            "route": "/a",
            "content_hash": "a" * 64,
            "review_status": "approved",
            "payload": {"id": "screen:a", "title": "Consulta"},
        }
    ]
    docs, _, _, _ = builder.build(entries, erp=erp, knowledge_version="v1")
    repo.sync(docs, [[1.0, 0.0]], erp_id="erp:one", knowledge_version="v1")
    repo.sync(docs, [[1.0, 0.0]], erp_id="erp:one", knowledge_version="v1")
    other = type(docs[0])(
        document_id("erp:two", "v1", "screen:b"),
        docs[0].text,
        {**docs[0].metadata, "erp_id": "erp:two", "canonical_id": "screen:b"},
    )
    repo.sync([other], [[0.0, 1.0]], erp_id="erp:two", knowledge_version="v1")
    _, removed = repo.sync([], [], erp_id="erp:one", knowledge_version="v1")
    assert removed == 1 and repo.collection.count() == 1
    results = repo.query([0.0, 1.0], top_k=5, erp_id="erp:two", knowledge_version="v1")
    assert [r["canonical_id"] for r in results] == ["screen:b"]
    assert results == sorted(results, key=lambda row: row["distance"])


def test_ollama_batch_dimensions_and_clear_errors():
    def handler(request):
        assert request.url.path == "/api/embed"
        request.read()
        return httpx.Response(200, json={"embeddings": [[1.0, 2.0], [3.0, 4.0]]})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="http://ollama.test") as client:
        embeddings = OllamaEmbeddingClient(
            OllamaEmbeddingSettings(url="http://ollama.test"), client=client
        )
        assert embeddings.embed(["uno", "dos"]) == [[1.0, 2.0], [3.0, 4.0]]
        assert embeddings.dimensions == 2

    def broken(_request):
        raise httpx.ConnectError("offline")

    with httpx.Client(
        transport=httpx.MockTransport(broken), base_url="http://ollama.test"
    ) as client:
        with pytest.raises(OllamaEmbeddingError, match="No se pudieron"):
            OllamaEmbeddingClient(
                OllamaEmbeddingSettings(url="http://ollama.test"), client=client
            ).embed("hola")


def test_ollama_embedding_batches_preserve_order():
    calls = []

    def handler(request):
        request.read()
        payload = json.loads(request.content)
        values = payload["input"]
        calls.append(values)
        return httpx.Response(
            200,
            json={
                "embeddings": [
                    [float(value.rsplit("-", 1)[1]), 1.0]
                    for value in values
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    settings = OllamaEmbeddingSettings(
        url="http://ollama.test",
        batch_size=2,
    )
    with httpx.Client(transport=transport, base_url="http://ollama.test") as client:
        embeddings = OllamaEmbeddingClient(settings, client=client)
        vectors = embeddings.embed([f"texto-{index}" for index in range(5)])

    assert calls == [["texto-0", "texto-1"], ["texto-2", "texto-3"], ["texto-4"]]
    assert vectors == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0], [3.0, 1.0], [4.0, 1.0]]
    assert embeddings.dimensions == 2


def test_run_uses_fake_embedding_and_only_chromadb_job(chroma_session, tmp_path):
    _approve_correct_reject(chroma_session)
    jobs_before = {job.target: job.attempt_count for job in chroma_session.scalars(select(SyncJob))}
    repo = ChromaRepository(client=chromadb.PersistentClient(path=str(tmp_path / "run")))
    result = ChromaSyncService(chroma_session, repository=repo, embeddings=FakeEmbeddings()).run()
    jobs_after = {job.target: job.attempt_count for job in chroma_session.scalars(select(SyncJob))}
    assert result.status == "succeeded" and repo.collection.count() == 2
    assert result.summary["embedding_dimensions"] == 3
    assert jobs_after[SyncTarget.CHROMADB] == jobs_before[SyncTarget.CHROMADB] + 1
    assert jobs_after[SyncTarget.NEO4J] == jobs_before[SyncTarget.NEO4J]


def test_run_requests_version_lock_before_embedding_and_sync(
    chroma_session, tmp_path, monkeypatch
):
    _approve_correct_reject(chroma_session)
    repository = ChromaRepository(
        client=chromadb.PersistentClient(path=str(tmp_path / "locked-run"))
    )
    service = ChromaSyncService(
        chroma_session, repository=repository, embeddings=FakeEmbeddings()
    )
    original = ChromaSyncService._version
    lock_requests = []

    def observed(self, erp_id, knowledge_version, *, for_update=False):
        lock_requests.append(for_update)
        return original(self, erp_id, knowledge_version, for_update=for_update)

    monkeypatch.setattr(ChromaSyncService, "_version", observed)
    chroma_session.rollback()
    result = service.run()

    assert result.status == "succeeded"
    assert lock_requests[0] is True


def test_run_refuses_explicit_archived_version(chroma_session, tmp_path):
    version = chroma_session.scalar(select(KnowledgeVersionRecord))
    assert version is not None
    erp_id = version.erp_id
    knowledge_version = version.knowledge_version
    chroma_session.rollback()
    with chroma_session.begin():
        version = chroma_session.scalar(select(KnowledgeVersionRecord))
        version.status = KnowledgeVersionStatus.ARCHIVED

    repository = ChromaRepository(
        client=chromadb.PersistentClient(path=str(tmp_path / "archived-run"))
    )
    with pytest.raises(ValueError, match="dejó de ser ACTIVE"):
        ChromaSyncService(
            chroma_session, repository=repository, embeddings=FakeEmbeddings()
        ).run(erp_id=erp_id, knowledge_version=knowledge_version)


def test_running_job_is_rejected_before_documents_are_prepared(chroma_session, tmp_path):
    with chroma_session.begin():
        job = chroma_session.scalar(
            select(SyncJob).where(SyncJob.target == SyncTarget.CHROMADB)
        )
        job.status = SyncStatus.RUNNING
    service = ChromaSyncService(
        chroma_session,
        repository=ChromaRepository(
            client=chromadb.PersistentClient(path=str(tmp_path / "running"))
        ),
        embeddings=FakeEmbeddings(),
    )
    service.prepare = lambda **_kwargs: pytest.fail("prepare no debe ejecutarse")
    with pytest.raises(ValueError, match="ya está en ejecución"):
        service.run()
