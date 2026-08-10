from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.base import Base
from src.database.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    SyncStatus,
    SyncTarget,
)
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SyncJob,
)
from src.knowledge.canonical.enums import ReviewStatus
from src.pipeline.chroma_sync_job_executor import (
    ChromaSyncJobExecutionError,
    ChromaSyncJobExecutor,
)
from src.pipeline.neo4j_sync_job_executor import (
    Neo4jSyncJobExecutionError,
    Neo4jSyncJobExecutor,
)


class FakeNeo4jRepository:
    def __init__(self):
        self.bootstrapped = False
        self.replaced = []
        self.nodes = []
        self.relationships = []

    def bootstrap(self):
        self.bootstrapped = True

    def replace_version(self, erp_id, knowledge_version):
        self.replaced.append((erp_id, knowledge_version))

    def upsert_nodes(self, nodes, *, batch_size=200):
        self.nodes = list(nodes)
        return len(self.nodes)

    def upsert_relationships(self, relationships, *, batch_size=200):
        self.relationships = list(relationships)
        return len(self.relationships)


class FakeChromaRepository:
    def __init__(self):
        self.documents = []
        self.embeddings = []

    def sync(self, documents, embeddings, *, erp_id, knowledge_version):
        self.documents = list(documents)
        self.embeddings = list(embeddings)
        return len(self.documents), 0


class FakeEmbeddings:
    model = "fake-embedding"

    def __init__(self):
        self.dimensions = None

    def embed(self, texts):
        values = list(texts)
        self.dimensions = 3
        return [[1.0, 0.0, 0.0] for _ in values]


def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed_active(factory):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:projection-test",
            slug="projection-test",
            name="ERP Projection Test",
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
        item = KnowledgeItem(
            knowledge_version=version,
            canonical_id="screen:test",
            entity_type="screen",
            title="Pantalla prueba",
            normalized_title="pantalla prueba",
            route="/test",
            content_hash="b" * 64,
            source_payload={
                "id": "screen:test",
                "title": "Pantalla prueba",
                "route": "/test",
                "description": "Pantalla aprobada de prueba",
            },
            generated_review_status=ReviewStatus.PENDING_REVIEW,
            current_review_status=ReviewStatus.APPROVED,
        )
        session.add_all(
            [
                item,
                SyncJob(
                    knowledge_version=version,
                    target=SyncTarget.NEO4J,
                    status=SyncStatus.SUCCEEDED,
                    attempt_count=1,
                ),
                SyncJob(
                    knowledge_version=version,
                    target=SyncTarget.CHROMADB,
                    status=SyncStatus.SUCCEEDED,
                    attempt_count=1,
                ),
            ]
        )
        session.flush()
        return str(version.id), erp.id, version.knowledge_version


def parameters(version_id, erp_id, knowledge_version):
    return {
        "active_only": True,
        "knowledge_version_id": version_id,
        "knowledge_version": knowledge_version,
        "erp_id": erp_id,
    }


def test_neo4j_executor_syncs_only_captured_active_version():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version = seed_active(factory)
    repository = FakeNeo4jRepository()
    progress = []
    result = Neo4jSyncJobExecutor(
        factory, repository_factory=lambda: repository
    ).execute(
        job_id="00000000-0000-0000-0000-000000000001",
        scope="version",
        target=knowledge_version,
        parameters={
            **parameters(version_id, erp_id, knowledge_version),
            "batch_size": 100,
            "replace_version": False,
        },
        progress=lambda stage, payload: progress.append((stage, payload)),
    )
    assert result["target"] == "neo4j"
    assert result["active_only"] is True
    assert result["knowledge_version"] == knowledge_version
    assert result["eligible_items"] == 1
    assert result["nodes"] == 1
    assert repository.bootstrapped is True
    assert repository.replaced == []
    assert progress[-1][0] == "neo4j_synced"
    engine.dispose()


def test_chroma_executor_syncs_only_captured_active_version():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version = seed_active(factory)
    repository = FakeChromaRepository()
    result = ChromaSyncJobExecutor(
        factory,
        repository_factory=lambda: repository,
        embeddings_factory=FakeEmbeddings,
    ).execute(
        job_id="00000000-0000-0000-0000-000000000002",
        scope="version",
        target=knowledge_version,
        parameters=parameters(version_id, erp_id, knowledge_version),
        progress=lambda *_: None,
    )
    assert result["target"] == "chromadb"
    assert result["active_only"] is True
    assert result["eligible_items"] == 1
    assert result["documents"] == 1
    assert result["embedding_model"] == "fake-embedding"
    assert result["embedding_dimensions"] == 3
    assert len(repository.documents) == 1
    engine.dispose()


@pytest.mark.parametrize(
    ("executor_factory", "error_type"),
    [
        (
            lambda factory: Neo4jSyncJobExecutor(
                factory, repository_factory=FakeNeo4jRepository
            ),
            Neo4jSyncJobExecutionError,
        ),
        (
            lambda factory: ChromaSyncJobExecutor(
                factory,
                repository_factory=FakeChromaRepository,
                embeddings_factory=FakeEmbeddings,
            ),
            ChromaSyncJobExecutionError,
        ),
    ],
)
def test_projection_executor_refuses_version_that_is_no_longer_active(executor_factory, error_type):
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version = seed_active(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(version_id))
        version.status = KnowledgeVersionStatus.ARCHIVED

    with pytest.raises(error_type, match="dejó de ser ACTIVE"):
        executor_factory(factory).execute(
            job_id="00000000-0000-0000-0000-000000000003",
            scope="version",
            target=knowledge_version,
            parameters=parameters(version_id, erp_id, knowledge_version),
            progress=lambda *_: None,
        )
    engine.dispose()


class FailingNeo4jRepository(FakeNeo4jRepository):
    def upsert_nodes(self, nodes, *, batch_size=200):
        raise RuntimeError("neo4j failure")


class FailingChromaRepository(FakeChromaRepository):
    def sync(self, documents, embeddings, *, erp_id, knowledge_version):
        raise RuntimeError("chroma failure")


def test_failed_projection_persists_underlying_sync_job_failure_before_pipeline_fails():
    engine, factory = build_factory()
    version_id, erp_id, knowledge_version = seed_active(factory)
    params = parameters(version_id, erp_id, knowledge_version)

    with pytest.raises(Neo4jSyncJobExecutionError):
        Neo4jSyncJobExecutor(
            factory, repository_factory=FailingNeo4jRepository
        ).execute(
            job_id="00000000-0000-0000-0000-000000000004",
            scope="version",
            target=knowledge_version,
            parameters={**params, "replace_version": False, "batch_size": 200},
            progress=lambda *_: None,
        )
    with factory() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(version_id))
        neo_job = next(job for job in version.sync_jobs if job.target == SyncTarget.NEO4J)
        assert neo_job.status == SyncStatus.FAILED

    with pytest.raises(ChromaSyncJobExecutionError):
        ChromaSyncJobExecutor(
            factory,
            repository_factory=FailingChromaRepository,
            embeddings_factory=FakeEmbeddings,
        ).execute(
            job_id="00000000-0000-0000-0000-000000000005",
            scope="version",
            target=knowledge_version,
            parameters=params,
            progress=lambda *_: None,
        )
    with factory() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(version_id))
        chroma_job = next(job for job in version.sync_jobs if job.target == SyncTarget.CHROMADB)
        assert chroma_job.status == SyncStatus.FAILED
    engine.dispose()
