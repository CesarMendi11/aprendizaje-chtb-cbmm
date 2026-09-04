"""Add semantic HITL provenance and review timing."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260904_01"
down_revision = "20260821_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "semantic_review_actions",
        sa.Column(
            "human_added_claims",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "semantic_review_actions",
        sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "semantic_review_actions",
        sa.Column("review_duration_ms", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "review_duration_ms_nonnegative",
        "semantic_review_actions",
        "review_duration_ms IS NULL OR review_duration_ms >= 0",
    )
    op.alter_column(
        "semantic_review_actions",
        "human_added_claims",
        server_default=None,
    )


def downgrade():
    op.drop_constraint(
        "review_duration_ms_nonnegative",
        "semantic_review_actions",
        type_="check",
    )
    op.drop_column("semantic_review_actions", "review_duration_ms")
    op.drop_column("semantic_review_actions", "review_started_at")
    op.drop_column("semantic_review_actions", "human_added_claims")
