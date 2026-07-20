from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.enums import SyncStatus, SyncTarget
from src.database.models import KnowledgeItem, SyncJob
from src.database.services import CanonicalImportService, KnowledgeReviewService, Neo4jSyncService
from src.knowledge.canonical.builder import CanonicalKnowledgeBuilder
from src.knowledge.canonical.exporter import CanonicalKnowledgeExporter
from tests.canonical_fixtures import fictional_artifacts, fictional_profile


class FakeGraphRepository:
    def __init__(self, fail=False):
        self.fail = fail
        self.nodes = set()
        self.relationships = set()
        self.bootstrap_calls = 0
        self.replacements = []

    def bootstrap(self):
        self.bootstrap_calls += 1

    def replace_version(self, erp_id, version):
        self.replacements.append((erp_id, version))

    def upsert_nodes(self, nodes, *, batch_size):
        if self.fail:
            raise RuntimeError("token=synthetic-secret-value-that-must-not-survive")
        self.nodes.update(node.key for node in nodes)
        return len(nodes)

    def upsert_relationships(self, relationships, *, batch_size):
        self.relationships.update(rel.key for rel in relationships)
        return len(relationships)


@pytest.fixture
def graph_session(tmp_path):
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


def _items(session):
    erp = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "erp_system"))
    screen = session.scalar(
        select(KnowledgeItem).where(KnowledgeItem.entity_type == "screen").limit(1)
    )
    other = session.scalar(
        select(KnowledgeItem).where(KnowledgeItem.entity_type == "field").limit(1)
    )
    session.rollback()
    return erp, screen, other


def _approve_and_correct(session):
    erp, screen, other = _items(session)
    review = KnowledgeReviewService(session)
    with session.begin():
        review.approve(erp.id)
    correction = {
        key: value
        for key, value in screen.source_payload.items()
        if key not in {"review_status", "reviewed_at", "reviewed_by", "review_notes"}
    }
    correction["description"] = "Synthetic corrected description"
    session.rollback()
    with session.begin():
        review.correct(screen.id, correction, notes="Synthetic correction")
        review.reject(other.id, notes="Synthetic rejection")
    return erp, screen, other


def test_prepare_selects_only_approved_corrected_and_uses_effective_payload(graph_session):
    erp, screen, other = _approve_and_correct(graph_session)
    plan = Neo4jSyncService(graph_session).prepare()
    assert plan.eligible_items == 2
    assert plan.items_by_type == {"erp_system": 1, "screen": 1}
    projected = {node.properties["canonical_id"]: node for node in plan.nodes}
    assert (
        projected[screen.canonical_id].properties["description"]
        == "Synthetic corrected description"
    )
    assert projected[screen.canonical_id].properties["review_status"] == "corrected"
    assert other.canonical_id not in projected
    assert all(
        node.properties["review_status"] not in {"pending_review", "rejected"}
        for node in plan.nodes
    )


def test_dry_plan_does_not_touch_jobs_or_graph(graph_session):
    jobs_before = [
        (job.id, job.status, job.attempt_count) for job in graph_session.scalars(select(SyncJob))
    ]
    graph_session.rollback()
    plan = Neo4jSyncService(graph_session).prepare()
    jobs_after = [
        (job.id, job.status, job.attempt_count) for job in graph_session.scalars(select(SyncJob))
    ]
    assert plan.eligible_items == 0
    assert jobs_after == jobs_before


def test_empty_execution_rejected_unless_allow_empty(graph_session):
    repo = FakeGraphRepository()
    with pytest.raises(ValueError, match="allow-empty"):
        Neo4jSyncService(graph_session, repository=repo).run()
    graph_session.rollback()
    result = Neo4jSyncService(graph_session, repository=repo).run(allow_empty=True)
    assert result.status == "succeeded" and repo.bootstrap_calls == 1


def test_sync_job_transitions_idempotence_and_chromadb_is_untouched(graph_session):
    _approve_and_correct(graph_session)
    repo = FakeGraphRepository()
    graph_session.rollback()
    first = Neo4jSyncService(graph_session, repository=repo).run(batch_size=1, replace_version=True)
    node_count, rel_count = len(repo.nodes), len(repo.relationships)
    neo_job = graph_session.scalar(select(SyncJob).where(SyncJob.target == SyncTarget.NEO4J))
    chroma_job = graph_session.scalar(select(SyncJob).where(SyncJob.target == SyncTarget.CHROMADB))
    assert first.status == "succeeded" and neo_job.status == SyncStatus.SUCCEEDED
    assert neo_job.attempt_count == 1 and neo_job.checkpoint["eligible_items"] == 2
    assert first.summary["job_status"] == "succeeded"
    assert first.summary["sync_job"] == {
        "id": str(neo_job.id),
        "status": "succeeded",
        "attempt_count": 1,
        "checkpoint": neo_job.checkpoint,
    }
    first_projection_hash = first.summary["projection_hash"]
    assert chroma_job.status == SyncStatus.PENDING and chroma_job.attempt_count == 0
    graph_session.commit()
    second = Neo4jSyncService(graph_session, repository=repo).run(batch_size=1)
    assert (
        second.status == "succeeded"
        and len(repo.nodes) == node_count
        and len(repo.relationships) == rel_count
    )
    assert neo_job.attempt_count == 2
    assert second.summary["sync_job"]["status"] == "succeeded"
    assert second.summary["sync_job"]["attempt_count"] == 2
    assert second.summary["sync_job"]["checkpoint"] == neo_job.checkpoint
    assert second.summary["projection_hash"] == first_projection_hash
    assert second.summary["sync_job"]["checkpoint"]["projection_hash"] == first_projection_hash
    assert repo.replacements and repo.replacements[0][1] == first.summary["knowledge_version"]


def test_failure_is_sanitized_and_only_neo4j_job_fails(graph_session):
    _approve_and_correct(graph_session)
    graph_session.rollback()
    result = Neo4jSyncService(graph_session, repository=FakeGraphRepository(fail=True)).run()
    neo_job = graph_session.scalar(select(SyncJob).where(SyncJob.target == SyncTarget.NEO4J))
    chroma_job = graph_session.scalar(select(SyncJob).where(SyncJob.target == SyncTarget.CHROMADB))
    assert result.status == "failed" and neo_job.status == SyncStatus.FAILED
    assert result.summary["job_status"] == "failed"
    assert result.summary["sync_job"]["status"] == "failed"
    assert result.summary["sync_job"]["attempt_count"] == 1
    assert result.summary["sync_job"]["checkpoint"] == neo_job.checkpoint
    assert "synthetic-secret" not in (neo_job.error_summary or "")
    assert "synthetic-secret" not in result.summary["error"]
    assert chroma_job.status == SyncStatus.PENDING
