from sqlalchemy import create_engine

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.models import ReviewAction


def test_expected_database_tables_and_constraints():
    assert set(Base.metadata.tables) == {
        "erp_systems",
        "import_runs",
        "knowledge_versions",
        "knowledge_version_promotions",
        "knowledge_items",
        "pipeline_jobs",
        "removal_reconciliation_decisions",
        "removal_reconciliation_review_actions",
        "removal_reconciliation_review_sets",
        "review_actions",
        "semantic_proposals",
        "semantic_review_actions",
        "sync_jobs",
    }
    item = Base.metadata.tables["knowledge_items"]
    assert {"canonical_id", "source_payload", "content_hash", "review_revision"} <= set(
        item.c.keys()
    )
    assert any(c.name == "uq_knowledge_items_knowledge_version_id" for c in item.constraints)

    promotion = Base.metadata.tables["knowledge_version_promotions"]
    assert {
        "knowledge_version_id",
        "previous_active_version_id",
        "reviewer_subject",
        "reason",
        "source",
        "gate_snapshot",
    } <= set(promotion.c.keys())
    assert any(
        c.name == "uq_knowledge_version_promotions_version"
        for c in promotion.constraints
    )

    removal_set = Base.metadata.tables["removal_reconciliation_review_sets"]
    assert any(
        c.name == "uq_removal_reconciliation_review_sets_candidate"
        for c in removal_set.constraints
    )

    removal_decision = Base.metadata.tables["removal_reconciliation_decisions"]
    assert any(
        c.name == "uq_removal_reconciliation_decision_identity"
        for c in removal_decision.constraints
    )
    assert any(
        c.name == "ck_removal_reconciliation_decisions_removal_decision_candidate_item_absent"
        for c in removal_decision.constraints
    )

    removal_action = Base.metadata.tables["removal_reconciliation_review_actions"]
    assert any(
        c.name == "ck_removal_reconciliation_review_actions_removal_review_action_matches_decision"
        for c in removal_action.constraints
    )


def test_metadata_is_sqlite_portable():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert ReviewAction.__tablename__ == "review_actions"
