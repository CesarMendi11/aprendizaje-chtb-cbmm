"""Promotion Gate: auditoría de promoción de KnowledgeVersion."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260813_01"
down_revision = "20260810_01"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "knowledge_version_promotions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "knowledge_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "previous_active_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("reviewer_subject", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("gate_snapshot", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "knowledge_version_id",
            name="uq_knowledge_version_promotions_version",
        ),
    )
    op.create_index(
        "ix_knowledge_version_promotions_created_at",
        "knowledge_version_promotions",
        ["created_at"],
    )


def downgrade():
    op.drop_table("knowledge_version_promotions")
