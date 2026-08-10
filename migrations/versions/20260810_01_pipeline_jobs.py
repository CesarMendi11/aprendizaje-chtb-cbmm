"""Consola administrativa: jobs persistentes del pipeline."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260810_01"
down_revision = "20260721_01"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("target", sa.String(1000)),
        sa.Column("profile_name", sa.String(240)),
        sa.Column("erp_id", sa.String(160), sa.ForeignKey("erp_systems.id")),
        sa.Column(
            "knowledge_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id"),
        ),
        sa.Column("request_source", sa.String(60), nullable=False),
        sa.Column("parameters", JSON, nullable=False),
        sa.Column("stage", sa.String(120), nullable=False),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer()),
        sa.Column("checkpoint", JSON, nullable=False),
        sa.Column("result_payload", JSON),
        sa.Column("error_summary", sa.String(500)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "progress_current >= 0",
            name="progress_current_nonnegative",
        ),
        sa.CheckConstraint(
            "progress_total IS NULL OR progress_total >= 0",
            name="progress_total_nonnegative",
        ),
        sa.CheckConstraint(
            "progress_total IS NULL OR progress_current <= progress_total",
            name="progress_within_total",
        ),
    )
    op.create_index("ix_pipeline_jobs_status_kind", "pipeline_jobs", ["status", "kind"])
    op.create_index("ix_pipeline_jobs_requested_at", "pipeline_jobs", ["requested_at"])
    op.create_index("ix_pipeline_jobs_erp_id", "pipeline_jobs", ["erp_id"])
    op.create_index(
        "ix_pipeline_jobs_knowledge_version_id",
        "pipeline_jobs",
        ["knowledge_version_id"],
    )


def downgrade():
    op.drop_table("pipeline_jobs")
