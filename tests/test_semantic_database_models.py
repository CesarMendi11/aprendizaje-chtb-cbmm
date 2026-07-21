from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.enums import ReviewActionType, SemanticType
from src.database.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.validator import SUPPORTED_SCHEMA_VERSIONS

HASH = "a" * 64


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def proposal(**overrides):
    values = {
        "semantic_id": f"semantic:{uuid.uuid4().hex}",
        "knowledge_version_id": uuid.uuid4(),
        "screen_knowledge_item_id": uuid.uuid4(),
        "semantic_type": SemanticType.SCREEN_PURPOSE,
        "source_payload": {"purpose_summary": "Consulta funcional"},
        "source_content_hash": HASH,
        "evidence_payload": {"screen_id": "screen:test"},
        "evidence_hash": "b" * 64,
        "evidence_ids": ["evidence:test"],
        "generation_model": "test-model",
        "prompt_version": "screen-purpose-v1",
        "prompt_hash": "c" * 64,
        "generation_parameters": {},
        "generation_parameters_hash": "d" * 64,
    }
    values.update(overrides)
    return SemanticProposal(**values)


def action(proposal_id, **overrides):
    values = {
        "semantic_proposal_id": proposal_id,
        "action": ReviewActionType.APPROVE,
        "previous_status": ReviewStatus.PENDING_REVIEW,
        "new_status": ReviewStatus.APPROVED,
        "corrected_payload": None,
        "reviewer_subject": "cli:local",
        "proposal_content_hash": HASH,
        "source": "cli",
    }
    values.update(overrides)
    return SemanticReviewAction(**values)


def test_semantic_tables_columns_foreign_keys_constraints_and_indexes():
    assert {"semantic_proposals", "semantic_review_actions"} <= set(Base.metadata.tables)
    proposals = Base.metadata.tables["semantic_proposals"]
    actions = Base.metadata.tables["semantic_review_actions"]
    required = {
        "semantic_id",
        "knowledge_version_id",
        "screen_knowledge_item_id",
        "semantic_type",
        "source_payload",
        "source_content_hash",
        "evidence_payload",
        "evidence_hash",
        "evidence_ids",
        "generation_model",
        "prompt_version",
        "prompt_hash",
        "generation_parameters",
        "generation_parameters_hash",
        "current_review_status",
        "review_revision",
        "created_at",
        "updated_at",
    }
    assert required <= set(proposals.c.keys())
    assert all(not proposals.c[name].nullable for name in required)
    assert actions.c.corrected_payload.nullable
    assert not actions.c.reviewer_subject.nullable
    assert {fk.target_fullname for fk in proposals.foreign_keys} == {
        "knowledge_versions.id",
        "knowledge_items.id",
    }
    assert {fk.ondelete for fk in proposals.foreign_keys} == {"RESTRICT"}
    assert {fk.target_fullname for fk in actions.foreign_keys} == {"semantic_proposals.id"}
    constraint_names = {constraint.name for constraint in proposals.constraints}
    assert {
        "uq_semantic_proposals_semantic_id",
        "uq_semantic_proposals_generation_identity",
        "ck_semantic_proposals_review_revision_nonnegative",
        "ck_semantic_proposals_source_hash_length",
        "ck_semantic_proposals_evidence_hash_length",
        "ck_semantic_proposals_prompt_hash_length",
        "ck_semantic_proposals_generation_parameters_hash_length",
        "ck_semantic_proposals_review_status_supported",
    } <= constraint_names
    assert {index.name for index in proposals.indexes} == {
        "ix_semantic_proposals_version_status",
        "ix_semantic_proposals_screen_type",
        "ix_semantic_proposals_version_type_status",
        "ix_semantic_proposals_evidence_hash",
    }


def test_defaults_semantic_type_and_relationships(session):
    item = proposal()
    session.add(item)
    session.flush()
    assert item.current_review_status == ReviewStatus.PENDING_REVIEW
    assert item.review_revision == 0
    assert item.semantic_type == SemanticType.SCREEN_PURPOSE
    review = action(item.id)
    item.review_actions.append(review)
    assert review.semantic_proposal is item
    version = KnowledgeVersionRecord()
    screen = KnowledgeItem()
    version.semantic_proposals.append(item)
    screen.semantic_proposals.append(item)
    assert item.knowledge_version is version
    assert item.screen_knowledge_item is screen


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_content_hash", "x" * 63),
        ("evidence_hash", "x" * 65),
        ("prompt_hash", ""),
        ("generation_parameters_hash", "x" * 63),
    ],
)
def test_hash_lengths_are_database_constraints(session, field, value):
    session.add(proposal(**{field: value}))
    with pytest.raises(IntegrityError):
        session.flush()


def test_semantic_id_and_generation_identity_are_unique(session):
    first = proposal()
    session.add(first)
    session.flush()
    duplicate_id = proposal(semantic_id=first.semantic_id)
    session.add(duplicate_id)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    first = proposal(semantic_id="semantic:first")
    duplicate_generation = proposal(
        semantic_id="semantic:second",
        knowledge_version_id=first.knowledge_version_id,
        screen_knowledge_item_id=first.screen_knowledge_item_id,
        semantic_type=first.semantic_type,
        evidence_hash=first.evidence_hash,
        prompt_hash=first.prompt_hash,
        generation_model=first.generation_model,
        generation_parameters_hash=first.generation_parameters_hash,
    )
    session.add_all([first, duplicate_generation])
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.parametrize(
    "field",
    [
        "semantic_id",
        "knowledge_version_id",
        "screen_knowledge_item_id",
        "semantic_type",
        "source_payload",
        "source_content_hash",
        "evidence_payload",
        "evidence_hash",
        "evidence_ids",
        "generation_model",
        "prompt_version",
        "prompt_hash",
        "generation_parameters",
        "generation_parameters_hash",
        "created_at",
    ],
)
def test_proposal_identity_evidence_and_generation_metadata_are_immutable(session, field):
    item = proposal()
    session.add(item)
    session.flush()
    replacement = {
        "knowledge_version_id": uuid.uuid4(),
        "screen_knowledge_item_id": uuid.uuid4(),
        "semantic_type": "unknown",
        "source_payload": {"changed": True},
        "evidence_payload": {"changed": True},
        "evidence_ids": ["changed"],
        "generation_parameters": {"temperature": 1},
        "created_at": item.created_at.replace(year=item.created_at.year - 1),
    }.get(field, "e" * 64 if "hash" in field else f"changed:{field}")
    setattr(item, field, replacement)
    with pytest.raises(ValueError, match="inmutables"):
        session.flush()


def test_review_status_and_revision_are_mutable(session):
    item = proposal()
    session.add(item)
    session.flush()
    item.current_review_status = ReviewStatus.APPROVED
    item.review_revision = 1
    session.flush()
    assert item.current_review_status == ReviewStatus.APPROVED
    assert item.review_revision == 1


def test_database_enum_checks_are_registered_in_metadata():
    proposals = Base.metadata.tables["semantic_proposals"]
    actions = Base.metadata.tables["semantic_review_actions"]
    assert "ck_semantic_proposals_review_status_supported" in {
        constraint.name for constraint in proposals.constraints
    }
    assert {
        "ck_semantic_review_actions_action_supported",
        "ck_semantic_review_actions_previous_status_supported",
        "ck_semantic_review_actions_new_status_supported",
    } <= {constraint.name for constraint in actions.constraints}


def test_semantic_review_action_is_append_only_and_requires_reviewer(session):
    item = proposal()
    session.add(item)
    session.flush()
    review = action(item.id)
    session.add(review)
    session.flush()
    review.review_notes = "changed"
    with pytest.raises(ValueError, match="historial inmutable"):
        session.flush()
    session.rollback()

    item = proposal()
    session.add(item)
    session.flush()
    review = action(item.id)
    session.add(review)
    session.flush()
    session.delete(review)
    with pytest.raises(ValueError, match="historial inmutable"):
        session.flush()
    session.rollback()

    item = proposal()
    session.add(item)
    session.flush()
    session.add(action(item.id, reviewer_subject=""))
    with pytest.raises(IntegrityError):
        session.flush()


def test_migration_is_incremental_and_has_complete_triggers_and_downgrade():
    path = Path("migrations/versions/20260721_01_semantic_proposals.py")
    spec = importlib.util.spec_from_file_location("semantic_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = path.read_text(encoding="utf-8")
    assert module.down_revision == "20260716_01"
    assert callable(module.upgrade) and callable(module.downgrade)
    assert "semantic_proposals_immutable" in source
    assert "semantic_review_actions_append_only" in source
    assert "DROP TRIGGER IF EXISTS" in source
    assert "DROP FUNCTION IF EXISTS" in source


def test_canonical_and_neo4j_contracts_remain_unchanged():
    assert set(CanonicalKnowledgeBase.model_fields) == {
        "schema_version",
        "knowledge_version",
        "generated_at",
        "generator_version",
        "source_profile",
        "source_artifacts",
        "source_artifact_hashes",
        "erp_system",
        "modules",
        "screens",
        "ui_states",
        "fields",
        "controls",
        "tables",
        "table_columns",
        "links",
        "events",
        "transitions",
        "evidence",
        "build_warnings",
        "statistics",
    }
    assert SUPPORTED_SCHEMA_VERSIONS == {"1.0.0"}
    from src.graph.mapper import LABELS
    from src.graph.repository import RELATIONSHIP_TYPES

    assert "semantic_proposal" not in LABELS
    assert "HAS_SEMANTIC_KNOWLEDGE" not in RELATIONSHIP_TYPES


def test_sqlite_metadata_contains_expected_foreign_key_targets():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    targets = {fk["referred_table"] for fk in inspector.get_foreign_keys("semantic_proposals")}
    assert targets == {"knowledge_versions", "knowledge_items"}
