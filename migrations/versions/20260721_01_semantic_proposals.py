"""Fase semántica 1: propuestas persistentes e historial de revisión."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260721_01"
down_revision = "20260716_01"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    op.create_table(
        "semantic_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("semantic_id", sa.String(240), nullable=False),
        sa.Column(
            "knowledge_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "screen_knowledge_item_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("semantic_type", sa.String(40), nullable=False),
        sa.Column("source_payload", JSON, nullable=False),
        sa.Column("source_content_hash", sa.String(64), nullable=False),
        sa.Column("evidence_payload", JSON, nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("evidence_ids", JSON, nullable=False),
        sa.Column("generation_model", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(120), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("generation_parameters", JSON, nullable=False),
        sa.Column("generation_parameters_hash", sa.String(64), nullable=False),
        sa.Column("current_review_status", sa.String(40), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("trim(semantic_id) <> ''", name="semantic_id_nonempty"),
        sa.CheckConstraint("trim(semantic_type) <> ''", name="semantic_type_nonempty"),
        sa.CheckConstraint("semantic_type = 'screen_purpose'", name="semantic_type_supported"),
        sa.CheckConstraint("trim(generation_model) <> ''", name="generation_model_nonempty"),
        sa.CheckConstraint("trim(prompt_version) <> ''", name="prompt_version_nonempty"),
        sa.CheckConstraint("review_revision >= 0", name="review_revision_nonnegative"),
        sa.CheckConstraint("length(source_content_hash) = 64", name="source_hash_length"),
        sa.CheckConstraint("length(evidence_hash) = 64", name="evidence_hash_length"),
        sa.CheckConstraint("length(prompt_hash) = 64", name="prompt_hash_length"),
        sa.CheckConstraint(
            "length(generation_parameters_hash) = 64",
            name="generation_parameters_hash_length",
        ),
        sa.CheckConstraint(
            "current_review_status IN ('pending_review', 'approved', 'rejected', 'corrected')",
            name="review_status_supported",
        ),
        sa.UniqueConstraint("semantic_id", name="uq_semantic_proposals_semantic_id"),
        sa.UniqueConstraint(
            "knowledge_version_id",
            "screen_knowledge_item_id",
            "semantic_type",
            "evidence_hash",
            "prompt_hash",
            "generation_model",
            "generation_parameters_hash",
            name="uq_semantic_proposals_generation_identity",
        ),
    )
    for name, columns in (
        ("ix_semantic_proposals_version_status", ["knowledge_version_id", "current_review_status"]),
        ("ix_semantic_proposals_screen_type", ["screen_knowledge_item_id", "semantic_type"]),
        (
            "ix_semantic_proposals_version_type_status",
            ["knowledge_version_id", "semantic_type", "current_review_status"],
        ),
        ("ix_semantic_proposals_evidence_hash", ["evidence_hash"]),
    ):
        op.create_index(name, "semantic_proposals", columns)

    op.create_table(
        "semantic_review_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "semantic_proposal_id",
            sa.Uuid(),
            sa.ForeignKey("semantic_proposals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("previous_status", sa.String(40), nullable=False),
        sa.Column("new_status", sa.String(40), nullable=False),
        sa.Column("corrected_payload", JSON),
        sa.Column("review_notes", sa.String(4000)),
        sa.Column("reviewer_subject", sa.String(240), nullable=False),
        sa.Column("proposal_content_hash", sa.String(64), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "trim(reviewer_subject) <> ''",
            name="reviewer_subject_nonempty",
        ),
        sa.CheckConstraint("trim(source) <> ''", name="source_nonempty"),
        sa.CheckConstraint(
            "length(proposal_content_hash) = 64",
            name="proposal_hash_length",
        ),
        sa.CheckConstraint(
            "action IN ('approve', 'reject', 'correct', 'reset_to_pending')",
            name="action_supported",
        ),
        sa.CheckConstraint(
            "previous_status IN ('pending_review', 'approved', 'rejected', 'corrected')",
            name="previous_status_supported",
        ),
        sa.CheckConstraint(
            "new_status IN ('pending_review', 'approved', 'rejected', 'corrected')",
            name="new_status_supported",
        ),
    )
    op.create_index(
        "ix_semantic_review_actions_proposal_created",
        "semantic_review_actions",
        ["semantic_proposal_id", "created_at"],
    )

    op.execute(
        "CREATE FUNCTION prevent_semantic_proposal_immutable_mutation() RETURNS trigger AS $$ "
        "BEGIN "
        "IF TG_OP = 'DELETE' THEN "
        "RAISE EXCEPTION 'semantic_proposals cannot be deleted'; "
        "END IF; "
        "IF ROW(NEW.semantic_id, NEW.knowledge_version_id, NEW.screen_knowledge_item_id, "
        "NEW.semantic_type, NEW.source_payload, NEW.source_content_hash, "
        "NEW.evidence_payload, NEW.evidence_hash, NEW.evidence_ids, NEW.generation_model, "
        "NEW.prompt_version, NEW.prompt_hash, NEW.generation_parameters, "
        "NEW.generation_parameters_hash, NEW.created_at) "
        "IS DISTINCT FROM ROW(OLD.semantic_id, OLD.knowledge_version_id, "
        "OLD.screen_knowledge_item_id, OLD.semantic_type, OLD.source_payload, "
        "OLD.source_content_hash, OLD.evidence_payload, OLD.evidence_hash, "
        "OLD.evidence_ids, OLD.generation_model, OLD.prompt_version, OLD.prompt_hash, "
        "OLD.generation_parameters, OLD.generation_parameters_hash, OLD.created_at) THEN "
        "RAISE EXCEPTION 'semantic_proposal immutable fields cannot change'; "
        "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER semantic_proposals_immutable BEFORE UPDATE OR DELETE "
        "ON semantic_proposals FOR EACH ROW "
        "EXECUTE FUNCTION prevent_semantic_proposal_immutable_mutation()"
    )
    op.execute(
        "CREATE FUNCTION prevent_semantic_review_action_mutation() RETURNS trigger AS $$ "
        "BEGIN RAISE EXCEPTION 'semantic_review_actions is append-only'; "
        "END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER semantic_review_actions_append_only BEFORE UPDATE OR DELETE "
        "ON semantic_review_actions FOR EACH ROW "
        "EXECUTE FUNCTION prevent_semantic_review_action_mutation()"
    )


def downgrade():
    op.execute(
        "DROP TRIGGER IF EXISTS semantic_review_actions_append_only ON semantic_review_actions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_semantic_review_action_mutation()")
    op.execute("DROP TRIGGER IF EXISTS semantic_proposals_immutable ON semantic_proposals")
    op.execute("DROP FUNCTION IF EXISTS prevent_semantic_proposal_immutable_mutation()")
    op.drop_table("semantic_review_actions")
    op.drop_table("semantic_proposals")
