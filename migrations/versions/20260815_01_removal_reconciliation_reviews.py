"""Removal HITL: persisted reconciliation review sets, decisions and actions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260815_01"
down_revision = "20260813_01"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "removal_reconciliation_review_sets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "candidate_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "active_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "erp_id",
            sa.String(160),
            sa.ForeignKey("erp_systems.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("candidate_knowledge_version", sa.String(120), nullable=False),
        sa.Column("active_knowledge_version", sa.String(120), nullable=False),
        sa.Column("candidate_origin", sa.String(80), nullable=False),
        sa.Column("raw_diff_totals", JSON, nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("decision_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "candidate_version_id",
            name="uq_removal_reconciliation_review_sets_candidate",
        ),
        sa.CheckConstraint(
            "candidate_version_id <> active_version_id",
            name="removal_review_versions_distinct",
        ),
        sa.CheckConstraint(
            "length(plan_hash) = 64",
            name="removal_review_plan_hash_length",
        ),
        sa.CheckConstraint(
            "decision_count >= 0",
            name="removal_review_decision_count_nonnegative",
        ),
    )
    op.create_index(
        "ix_removal_review_sets_erp_created",
        "removal_reconciliation_review_sets",
        ["erp_id", "created_at"],
    )

    op.create_table(
        "removal_reconciliation_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "review_set_id",
            sa.Uuid(),
            sa.ForeignKey("removal_reconciliation_review_sets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "active_item_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "candidate_item_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_items.id", ondelete="RESTRICT"),
        ),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("canonical_id", sa.String(200), nullable=False),
        sa.Column("screen_id", sa.String(200)),
        sa.Column("plan_reason", sa.String(240), nullable=False),
        sa.Column("removal_confirmation", sa.String(60)),
        sa.Column("proposed_decision", sa.String(40), nullable=False),
        sa.Column("current_decision", sa.String(40)),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("decision_fingerprint", sa.String(64), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "review_set_id",
            "entity_type",
            "canonical_id",
            name="uq_removal_reconciliation_decision_identity",
        ),
        sa.CheckConstraint(
            "review_revision >= 0",
            name="removal_decision_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "current_decision IS NULL OR current_decision IN "
            "('retain_from_active', 'confirmed_remove')",
            name="removal_current_decision_resolved",
        ),
        sa.CheckConstraint(
            "proposed_decision IN ('retain_from_active', 'confirmed_remove', 'unresolved')",
            name="removal_proposed_decision_supported",
        ),
        sa.CheckConstraint(
            "length(decision_fingerprint) = 64",
            name="removal_decision_fingerprint_length",
        ),
        sa.CheckConstraint(
            "candidate_item_id IS NULL",
            name="removal_decision_candidate_item_absent",
        ),
    )
    op.create_index(
        "ix_removal_decisions_set_current",
        "removal_reconciliation_decisions",
        ["review_set_id", "current_decision"],
    )

    op.create_table(
        "removal_reconciliation_review_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.Uuid(),
            sa.ForeignKey("removal_reconciliation_decisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous_decision", sa.String(40)),
        sa.Column("new_decision", sa.String(40)),
        sa.Column("review_notes", sa.Text(), nullable=False),
        sa.Column("reviewer_subject", sa.String(240), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("decision_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('confirm_retain', 'confirm_remove', 'reset_to_pending')",
            name="removal_review_action_supported",
        ),
        sa.CheckConstraint(
            "previous_decision IS NULL OR previous_decision IN "
            "('retain_from_active', 'confirmed_remove')",
            name="removal_review_previous_decision_resolved",
        ),
        sa.CheckConstraint(
            "new_decision IS NULL OR new_decision IN "
            "('retain_from_active', 'confirmed_remove')",
            name="removal_review_new_decision_resolved",
        ),
        sa.CheckConstraint(
            "(action = 'confirm_retain' AND previous_decision IS NULL "
            "AND new_decision = 'retain_from_active') OR "
            "(action = 'confirm_remove' AND previous_decision IS NULL "
            "AND new_decision = 'confirmed_remove') OR "
            "(action = 'reset_to_pending' AND previous_decision IS NOT NULL "
            "AND new_decision IS NULL)",
            name="removal_review_action_matches_decision",
        ),
        sa.CheckConstraint(
            "length(decision_fingerprint) = 64",
            name="removal_review_action_fingerprint_length",
        ),
        sa.CheckConstraint(
            "trim(review_notes) <> ''",
            name="removal_review_action_notes_nonempty",
        ),
        sa.CheckConstraint(
            "trim(reviewer_subject) <> ''",
            name="removal_review_action_reviewer_nonempty",
        ),
    )
    op.create_index(
        "ix_removal_review_actions_decision_created",
        "removal_reconciliation_review_actions",
        ["decision_id", "created_at"],
    )

    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_removal_review_history_mutation() "
        "RETURNS trigger AS $$ BEGIN "
        "RAISE EXCEPTION 'removal reconciliation review history is append-only'; "
        "END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER removal_review_sets_append_only BEFORE UPDATE OR DELETE ON "
        "removal_reconciliation_review_sets FOR EACH ROW EXECUTE FUNCTION "
        "prevent_removal_review_history_mutation()"
    )
    op.execute(
        "CREATE TRIGGER removal_review_actions_append_only BEFORE UPDATE OR DELETE ON "
        "removal_reconciliation_review_actions FOR EACH ROW EXECUTE FUNCTION "
        "prevent_removal_review_history_mutation()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION protect_removal_decision_provenance() "
        "RETURNS trigger AS $$ BEGIN "
        "IF TG_OP = 'DELETE' THEN "
        "RAISE EXCEPTION 'removal reconciliation decisions cannot be deleted'; "
        "END IF; "
        "IF NEW.review_set_id IS DISTINCT FROM OLD.review_set_id "
        "OR NEW.active_item_id IS DISTINCT FROM OLD.active_item_id "
        "OR NEW.candidate_item_id IS DISTINCT FROM OLD.candidate_item_id "
        "OR NEW.entity_type IS DISTINCT FROM OLD.entity_type "
        "OR NEW.canonical_id IS DISTINCT FROM OLD.canonical_id "
        "OR NEW.screen_id IS DISTINCT FROM OLD.screen_id "
        "OR NEW.plan_reason IS DISTINCT FROM OLD.plan_reason "
        "OR NEW.removal_confirmation IS DISTINCT FROM OLD.removal_confirmation "
        "OR NEW.proposed_decision IS DISTINCT FROM OLD.proposed_decision "
        "OR NEW.requires_human_review IS DISTINCT FROM OLD.requires_human_review "
        "OR NEW.decision_fingerprint IS DISTINCT FROM OLD.decision_fingerprint "
        "OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN "
        "RAISE EXCEPTION 'removal reconciliation decision provenance is immutable'; "
        "END IF; "
        "RETURN NEW; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER removal_decision_provenance_immutable BEFORE UPDATE OR DELETE ON "
        "removal_reconciliation_decisions FOR EACH ROW EXECUTE FUNCTION "
        "protect_removal_decision_provenance()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS removal_decision_provenance_immutable ON "
        "removal_reconciliation_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_removal_decision_provenance()")
    op.execute(
        "DROP TRIGGER IF EXISTS removal_review_actions_append_only ON "
        "removal_reconciliation_review_actions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS removal_review_sets_append_only ON "
        "removal_reconciliation_review_sets"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_removal_review_history_mutation()")
    op.drop_table("removal_reconciliation_review_actions")
    op.drop_table("removal_reconciliation_decisions")
    op.drop_table("removal_reconciliation_review_sets")
