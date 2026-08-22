"""PostgreSQL lexical and trigram indexes for canonical entity resolution."""

from alembic import op

revision = "20260821_01"
down_revision = "20260820_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_knowledge_items_entity_label_trgm "
        "ON knowledge_items USING gin "
        "((coalesce(normalized_title, lower(title), '')) gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_items_search_tsv "
        "ON knowledge_items USING gin ("
        "to_tsvector('simple', "
        "coalesce(title, '') || ' ' || "
        "coalesce(normalized_title, '') || ' ' || "
        "coalesce(route, ''))"
        ")"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_knowledge_items_search_tsv")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_items_entity_label_trgm")
