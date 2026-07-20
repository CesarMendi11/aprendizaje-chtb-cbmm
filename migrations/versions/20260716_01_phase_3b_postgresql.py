"""Fase 3B.1: persistencia y revisión de conocimiento."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260716_01"
down_revision = None
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "erp_systems",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("profile_name", sa.String(240), nullable=False),
        sa.Column("base_url", sa.String(1000)),
        sa.Column("adapter", sa.String(120)),
        sa.Column("safe_metadata", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_erp_systems_slug"),
    )
    op.create_table(
        "import_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("erp_id", sa.String(160), sa.ForeignKey("erp_systems.id"), nullable=False),
        sa.Column("source_knowledge_path", sa.String(1000), nullable=False),
        sa.Column("source_manifest_path", sa.String(1000), nullable=False),
        sa.Column("requested_knowledge_version", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("inserted_items", sa.Integer(), nullable=False),
        sa.Column("carried_reviews", sa.Integer(), nullable=False),
        sa.Column("skipped_items", sa.Integer(), nullable=False),
        sa.Column("warning_count", sa.Integer(), nullable=False),
        sa.Column("error_summary", sa.String(500)),
        sa.Column("source_hashes", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_runs_erp_id", "import_runs", ["erp_id"])
    op.create_index("ix_import_runs_requested_knowledge_version", "import_runs", ["requested_knowledge_version"])
    op.create_table(
        "knowledge_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("erp_id", sa.String(160), sa.ForeignKey("erp_systems.id"), nullable=False),
        sa.Column("import_run_id", sa.Uuid(), sa.ForeignKey("import_runs.id"), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("knowledge_version", sa.String(120), nullable=False),
        sa.Column("canonical_hash", sa.String(64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entity_counts", JSON, nullable=False),
        sa.Column("source_artifact_hashes", JSON, nullable=False),
        sa.Column("build_warnings", JSON, nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("erp_id", "knowledge_version", name="uq_knowledge_versions_erp_id"),
        sa.UniqueConstraint("import_run_id", name="uq_knowledge_versions_import_run_id"),
    )
    op.create_index("ix_knowledge_versions_erp_id", "knowledge_versions", ["erp_id"])
    op.create_index("ix_knowledge_versions_erp_status", "knowledge_versions", ["erp_id", "status"])
    op.execute(
        "CREATE UNIQUE INDEX uq_knowledge_versions_one_active "
        "ON knowledge_versions (erp_id) WHERE status = 'active'"
    )
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_version_id", sa.Uuid(), sa.ForeignKey("knowledge_versions.id"), nullable=False),
        sa.Column("canonical_id", sa.String(200), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("parent_canonical_id", sa.String(200)),
        sa.Column("title", sa.String(500)),
        sa.Column("normalized_title", sa.String(500)),
        sa.Column("route", sa.String(1000)),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("source_payload", JSON, nullable=False),
        sa.Column("generated_review_status", sa.String(40), nullable=False),
        sa.Column("current_review_status", sa.String(40), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("knowledge_version_id", "entity_type", "canonical_id", name="uq_knowledge_items_knowledge_version_id"),
    )
    for name, columns in (
        ("ix_knowledge_items_version_type", ["knowledge_version_id", "entity_type"]),
        ("ix_knowledge_items_version_status", ["knowledge_version_id", "current_review_status"]),
        ("ix_knowledge_items_route", ["route"]),
        ("ix_knowledge_items_canonical_id", ["canonical_id"]),
        ("ix_knowledge_items_parent", ["parent_canonical_id"]),
    ):
        op.create_index(name, "knowledge_items", columns)
    op.create_table(
        "review_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_item_id", sa.Uuid(), sa.ForeignKey("knowledge_items.id"), nullable=False),
        sa.Column("previous_item_id", sa.Uuid(), sa.ForeignKey("knowledge_items.id")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous_status", sa.String(40), nullable=False),
        sa.Column("new_status", sa.String(40), nullable=False),
        sa.Column("corrected_payload", JSON),
        sa.Column("review_notes", sa.Text()),
        sa.Column("reviewer_subject", sa.String(240)),
        sa.Column("item_content_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_actions_item_created", "review_actions", ["knowledge_item_id", "created_at"])
    op.execute(
        "CREATE FUNCTION prevent_review_action_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'review_actions is append-only'; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER review_actions_append_only BEFORE UPDATE OR DELETE ON review_actions "
        "FOR EACH ROW EXECUTE FUNCTION prevent_review_action_mutation()"
    )
    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("knowledge_version_id", sa.Uuid(), sa.ForeignKey("knowledge_versions.id"), nullable=False),
        sa.Column("target", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("checkpoint", JSON),
        sa.Column("error_summary", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt_count >= 0", name="ck_sync_jobs_sync_attempt_nonnegative"),
        sa.UniqueConstraint("knowledge_version_id", "target", name="uq_sync_jobs_knowledge_version_id"),
    )
    op.create_index("ix_sync_jobs_status_target", "sync_jobs", ["status", "target"])


def downgrade():
    op.drop_table("sync_jobs")
    op.execute("DROP TRIGGER IF EXISTS review_actions_append_only ON review_actions")
    op.execute("DROP FUNCTION IF EXISTS prevent_review_action_mutation()")
    op.drop_table("review_actions")
    op.drop_table("knowledge_items")
    op.execute("DROP INDEX IF EXISTS uq_knowledge_versions_one_active")
    op.drop_table("knowledge_versions")
    op.drop_table("import_runs")
    op.drop_table("erp_systems")
