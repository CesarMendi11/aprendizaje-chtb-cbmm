from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from src.database.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    SemanticType,
)
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
)
from src.knowledge.canonical.enums import ReviewStatus

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _test_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    database = urlsplit(url).path.lstrip("/").casefold()
    if "semantic_test" not in database:
        pytest.fail("TEST_DATABASE_URL no apunta a una base temporal con marcador seguro")
    return url


@pytest.fixture(scope="module")
def pg_engine():
    engine = sa.create_engine(_test_url(), pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("Las pruebas semánticas requieren PostgreSQL")
    yield engine
    engine.dispose()


@pytest.fixture
def semantic_graph(pg_engine):
    suffix = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    with Session(pg_engine, expire_on_commit=False) as session, session.begin():
        erp = ERPSystemRecord(
            id=f"erp:test:{suffix}",
            slug=f"test-{suffix}",
            name="Synthetic ERP",
            profile_name="semantic-test",
            safe_metadata={},
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="synthetic/knowledge.json",
            source_manifest_path="synthetic/manifest.json",
            requested_knowledge_version=f"test-{suffix}",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.0.0",
            knowledge_version=f"test-{suffix}",
            canonical_hash=HASH_A,
            generated_at=now,
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.IMPORTED,
        )
        screen = KnowledgeItem(
            knowledge_version=version,
            canonical_id=f"screen:{suffix}",
            entity_type="screen",
            title="Synthetic Screen",
            normalized_title="synthetic screen",
            route=f"/test/{suffix}",
            content_hash=HASH_A,
            source_payload={"id": f"screen:{suffix}", "title": "Synthetic Screen"},
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        proposal = SemanticProposal(
            semantic_id=f"semantic:{suffix}",
            knowledge_version=version,
            screen_knowledge_item=screen,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            source_payload={"purpose_summary": "Synthetic purpose"},
            source_content_hash=HASH_A,
            evidence_payload={"screen_id": screen.canonical_id},
            evidence_hash=HASH_B,
            evidence_ids=[f"evidence:{suffix}"],
            generation_model="synthetic-model",
            prompt_version="screen-purpose-v1",
            prompt_hash=HASH_C,
            generation_parameters={"temperature": 0, "stream": False},
            generation_parameters_hash=HASH_D,
        )
        session.add(proposal)
    return {
        "erp_id": erp.id,
        "run_id": run.id,
        "version_id": version.id,
        "screen_id": screen.id,
        "proposal_id": proposal.id,
        "semantic_id": proposal.semantic_id,
    }


def _rejected(engine, statement):
    with pytest.raises((IntegrityError, DBAPIError)):
        with engine.begin() as connection:
            connection.execute(statement)


@pytest.mark.postgresql
def test_postgresql_proposal_defaults_json_relations_and_mutability(pg_engine, semantic_graph):
    proposal_id = semantic_graph["proposal_id"]
    with Session(pg_engine) as session:
        proposal = session.get(SemanticProposal, proposal_id)
        assert proposal is not None
        assert proposal.current_review_status == ReviewStatus.PENDING_REVIEW
        assert proposal.review_revision == 0
        assert proposal.generation_parameters == {"temperature": 0, "stream": False}
        assert proposal.evidence_ids == [f"evidence:{semantic_graph['semantic_id'].split(':')[1]}"]
        assert proposal.knowledge_version.id == semantic_graph["version_id"]
        assert proposal.screen_knowledge_item.id == semantic_graph["screen_id"]

    proposals = sa.table(
        "semantic_proposals",
        sa.column("id"),
        sa.column("current_review_status"),
        sa.column("review_revision"),
        sa.column("source_payload"),
        sa.column("evidence_payload"),
        sa.column("semantic_id"),
        sa.column("knowledge_version_id"),
        sa.column("screen_knowledge_item_id"),
        sa.column("generation_model"),
        sa.column("prompt_version"),
        sa.column("prompt_hash"),
        sa.column("generation_parameters"),
    )
    with pg_engine.begin() as connection:
        connection.execute(
            sa.update(proposals)
            .where(proposals.c.id == proposal_id)
            .values(current_review_status="approved", review_revision=1)
        )
    changes = {
        "source_payload": {"changed": True},
        "evidence_payload": {"changed": True},
        "semantic_id": f"changed:{uuid.uuid4().hex}",
        "knowledge_version_id": uuid.uuid4(),
        "screen_knowledge_item_id": uuid.uuid4(),
        "generation_model": "changed-model",
        "prompt_version": "changed-prompt",
        "prompt_hash": "e" * 64,
        "generation_parameters": {"temperature": 1},
    }
    for field, value in changes.items():
        _rejected(
            pg_engine,
            sa.update(proposals).where(proposals.c.id == proposal_id).values({field: value}),
        )
    _rejected(pg_engine, sa.delete(proposals).where(proposals.c.id == proposal_id))


@pytest.mark.postgresql
def test_postgresql_proposal_constraints_and_foreign_keys(pg_engine, semantic_graph):
    metadata = sa.MetaData()
    proposals = sa.Table("semantic_proposals", metadata, autoload_with=pg_engine)
    base = {
        "id": uuid.uuid4(),
        "semantic_id": f"other:{uuid.uuid4().hex}",
        "knowledge_version_id": semantic_graph["version_id"],
        "screen_knowledge_item_id": semantic_graph["screen_id"],
        "semantic_type": "screen_purpose",
        "source_payload": {},
        "source_content_hash": HASH_A,
        "evidence_payload": {},
        "evidence_hash": HASH_B,
        "evidence_ids": [],
        "generation_model": "synthetic-model",
        "prompt_version": "screen-purpose-v1",
        "prompt_hash": HASH_C,
        "generation_parameters": {},
        "generation_parameters_hash": HASH_D,
        "current_review_status": "pending_review",
        "review_revision": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    for override in (
        {"semantic_id": semantic_graph["semantic_id"], "generation_model": "other"},
        {},
        {"source_content_hash": "short", "generation_model": "other"},
        {"semantic_type": "unknown", "generation_model": "other"},
        {"current_review_status": "unknown", "generation_model": "other"},
    ):
        values = {**base, "id": uuid.uuid4(), "semantic_id": f"other:{uuid.uuid4().hex}"}
        values.update(override)
        _rejected(pg_engine, sa.insert(proposals).values(**values))

    screens = sa.Table("knowledge_items", metadata, autoload_with=pg_engine)
    versions = sa.Table("knowledge_versions", metadata, autoload_with=pg_engine)
    _rejected(
        pg_engine,
        sa.delete(screens).where(screens.c.id == semantic_graph["screen_id"]),
    )
    _rejected(
        pg_engine,
        sa.delete(versions).where(versions.c.id == semantic_graph["version_id"]),
    )


@pytest.mark.postgresql
def test_postgresql_semantic_review_action_constraints_append_only_and_order(
    pg_engine, semantic_graph
):
    metadata = sa.MetaData()
    actions = sa.Table("semantic_review_actions", metadata, autoload_with=pg_engine)
    now = datetime.now(timezone.utc)
    first_id = uuid.uuid4()
    valid = {
        "id": first_id,
        "semantic_proposal_id": semantic_graph["proposal_id"],
        "action": "approve",
        "previous_status": "pending_review",
        "new_status": "approved",
        "corrected_payload": None,
        "review_notes": None,
        "reviewer_subject": "cli:local",
        "proposal_content_hash": HASH_A,
        "source": "cli",
        "created_at": now,
    }
    with pg_engine.begin() as connection:
        connection.execute(sa.insert(actions).values(**valid))
        row = (
            connection.execute(sa.select(actions).where(actions.c.id == first_id)).mappings().one()
        )
        assert row["corrected_payload"] is None

    for override in (
        {"reviewer_subject": ""},
        {"source": ""},
        {"proposal_content_hash": "short"},
        {"action": "unknown"},
        {"previous_status": "unknown"},
        {"new_status": "unknown"},
    ):
        values = {**valid, "id": uuid.uuid4(), **override}
        _rejected(pg_engine, sa.insert(actions).values(**values))

    _rejected(
        pg_engine,
        sa.update(actions).where(actions.c.id == first_id).values(review_notes="changed"),
    )
    _rejected(pg_engine, sa.delete(actions).where(actions.c.id == first_id))

    proposals = sa.Table("semantic_proposals", metadata, autoload_with=pg_engine)
    _rejected(
        pg_engine,
        sa.delete(proposals).where(proposals.c.id == semantic_graph["proposal_id"]),
    )
    second = {**valid, "id": uuid.uuid4(), "created_at": now + timedelta(microseconds=1)}
    with pg_engine.begin() as connection:
        connection.execute(sa.insert(actions).values(**second))
        ordered = (
            connection.execute(
                sa.select(actions.c.id)
                .where(actions.c.semantic_proposal_id == semantic_graph["proposal_id"])
                .order_by(actions.c.created_at, actions.c.id)
            )
            .scalars()
            .all()
        )
    assert ordered == [first_id, second["id"]]
