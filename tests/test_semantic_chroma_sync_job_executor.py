from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import ERPSystemRecord, ImportRun, KnowledgeVersionRecord
from src.pipeline.semantic_chroma_sync_job_executor import (
    SemanticChromaSyncJobExecutionError,
    SemanticChromaSyncJobExecutor,
)


@dataclass
class FakeResult:
    summary: dict


class FakeService:
    def __init__(self, session, **kwargs):
        self.repository = kwargs.get("repository")
        self.embeddings = kwargs.get("embeddings")

    def prepare(self, *, erp_id, knowledge_version):
        return object(), [object()], {
            "publishable_proposals": 1,
            "documents": 1,
            "skipped": 0,
        }

    def run(self, *, erp_id, knowledge_version):
        return FakeResult(
            {
                "erp_id": erp_id,
                "knowledge_version": knowledge_version,
                "publishable_proposals": 1,
                "documents": 1,
                "collection_name": "erp_assistant_semantic_v1",
                "embedding_model": "fake-embedding",
                "embedding_dimensions": 3,
                "inserted_or_updated": 1,
                "removed_stale": 0,
                "skipped": 0,
                "skipped_reasons": {},
            }
        )


class FakeRepository:
    pass


class FakeEmbeddings:
    model = "fake-embedding"


def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed_active(factory):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:semantic-executor",
            slug="semantic-executor",
            name="ERP Semantic Executor",
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
        session.add(version)
        session.flush()
        return str(version.id), erp.id, version.knowledge_version


def params(version_id, erp_id, knowledge_version):
    return {
        "active_only": True,
        "knowledge_version_id": version_id,
        "knowledge_version": knowledge_version,
        "erp_id": erp_id,
        "projection": "semantic_chromadb",
    }


def test_executor_syncs_semantics_only_for_captured_active_version():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version = seed_active(factory)
    progress = []
    executor = SemanticChromaSyncJobExecutor(
        factory,
        repository_factory=FakeRepository,
        embeddings_factory=FakeEmbeddings,
        service_factory=lambda session, **kwargs: FakeService(session, **kwargs),
    )
    result = executor.execute(
        job_id="00000000-0000-0000-0000-000000000001",
        scope="version",
        target=knowledge_version,
        parameters=params(version_id, erp_id, knowledge_version),
        progress=lambda stage, payload: progress.append((stage, payload)),
    )
    assert result["target"] == "semantic_chromadb"
    assert result["active_only"] is True
    assert result["knowledge_version"] == knowledge_version
    assert result["documents"] == 1
    assert result["inserted_or_updated"] == 1
    assert [stage for stage, _ in progress] == [
        "validating_active_version",
        "semantic_documents_prepared",
        "embedding_and_syncing_semantics",
        "semantic_chroma_synced",
    ]
    engine.dispose()


def test_executor_refuses_version_that_is_no_longer_active():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version = seed_active(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(version_id))
        version.status = KnowledgeVersionStatus.ARCHIVED

    executor = SemanticChromaSyncJobExecutor(
        factory,
        repository_factory=FakeRepository,
        embeddings_factory=FakeEmbeddings,
        service_factory=lambda session, **kwargs: FakeService(session, **kwargs),
    )
    with pytest.raises(SemanticChromaSyncJobExecutionError, match="dejó de ser ACTIVE"):
        executor.execute(
            job_id="00000000-0000-0000-0000-000000000002",
            scope="version",
            target=knowledge_version,
            parameters=params(version_id, erp_id, knowledge_version),
            progress=lambda *_: None,
        )
    engine.dispose()
