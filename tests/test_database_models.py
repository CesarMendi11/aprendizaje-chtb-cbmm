from sqlalchemy import create_engine

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.models import ReviewAction


def test_expected_database_tables_and_constraints():
    assert set(Base.metadata.tables) == {
        "erp_systems",
        "import_runs",
        "knowledge_versions",
        "knowledge_items",
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


def test_metadata_is_sqlite_portable():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    assert ReviewAction.__tablename__ == "review_actions"
