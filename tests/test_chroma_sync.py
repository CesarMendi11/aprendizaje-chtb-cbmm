from __future__ import annotations

import chromadb
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.config.ollama_settings import OllamaEmbeddingSettings
from src.database.base import Base
from src.database.enums import SyncTarget
from src.database.models import KnowledgeItem, SyncJob
from src.database.services import (
    CanonicalImportService,
    ChromaSyncService,
    KnowledgeReviewService,
    SafeDocumentBuilder,
)
from src.knowledge.canonical.builder import CanonicalKnowledgeBuilder
from src.knowledge.canonical.exporter import CanonicalKnowledgeExporter
from src.vectorstore import ChromaRepository, OllamaEmbeddingClient, OllamaEmbeddingError
from src.vectorstore.chroma_repository import collection_name, document_id
from tests.canonical_fixtures import fictional_artifacts, fictional_profile


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
    docs, _ = builder.build(entries, erp=erp, knowledge_version="v1")
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
