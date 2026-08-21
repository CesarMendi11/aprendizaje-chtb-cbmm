"""Semantic lifecycle provenance for cross-version carry-forward and reinference."""

import sqlalchemy as sa
from alembic import op

revision = "20260820_01"
down_revision = "20260815_01"
branch_labels = None
depends_on = None


def _replace_immutable_trigger_function(*, include_lineage: bool) -> None:
    if include_lineage:
        new_fields = (
            "NEW.lifecycle_origin, NEW.source_semantic_proposal_id, "
            "NEW.source_knowledge_version_id, NEW.source_review_status, "
            "NEW.source_review_revision, NEW.source_effective_content_hash, "
        )
        old_fields = (
            "OLD.lifecycle_origin, OLD.source_semantic_proposal_id, "
            "OLD.source_knowledge_version_id, OLD.source_review_status, "
            "OLD.source_review_revision, OLD.source_effective_content_hash, "
        )
    else:
        new_fields = ""
        old_fields = ""
    op.execute(
        "CREATE OR REPLACE FUNCTION prevent_semantic_proposal_immutable_mutation() "
        "RETURNS trigger AS $$ BEGIN "
        "IF TG_OP = 'DELETE' THEN "
        "RAISE EXCEPTION 'semantic_proposals cannot be deleted'; "
        "END IF; "
        "IF ROW(NEW.semantic_id, NEW.knowledge_version_id, NEW.screen_knowledge_item_id, "
        "NEW.semantic_type, NEW.source_payload, NEW.source_content_hash, "
        "NEW.evidence_payload, NEW.evidence_hash, NEW.evidence_ids, NEW.generation_model, "
        "NEW.prompt_version, NEW.prompt_hash, NEW.generation_parameters, "
        f"NEW.generation_parameters_hash, {new_fields}NEW.created_at) "
        "IS DISTINCT FROM ROW(OLD.semantic_id, OLD.knowledge_version_id, "
        "OLD.screen_knowledge_item_id, OLD.semantic_type, OLD.source_payload, "
        "OLD.source_content_hash, OLD.evidence_payload, OLD.evidence_hash, "
        "OLD.evidence_ids, OLD.generation_model, OLD.prompt_version, OLD.prompt_hash, "
        "OLD.generation_parameters, "
        f"OLD.generation_parameters_hash, {old_fields}OLD.created_at) THEN "
        "RAISE EXCEPTION 'semantic_proposal immutable fields cannot change'; "
        "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
    )


def upgrade():
    op.add_column(
        "semantic_proposals",
        sa.Column(
            "lifecycle_origin",
            sa.String(40),
            nullable=False,
            server_default="generated",
        ),
    )
    op.add_column(
        "semantic_proposals",
        sa.Column(
            "source_semantic_proposal_id",
            sa.Uuid(),
            sa.ForeignKey("semantic_proposals.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "semantic_proposals",
        sa.Column(
            "source_knowledge_version_id",
            sa.Uuid(),
            sa.ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
        ),
    )
    op.add_column(
        "semantic_proposals",
        sa.Column("source_review_status", sa.String(40)),
    )
    op.add_column(
        "semantic_proposals",
        sa.Column("source_review_revision", sa.Integer()),
    )
    op.add_column(
        "semantic_proposals",
        sa.Column("source_effective_content_hash", sa.String(64)),
    )

    for constraint in (
        sa.CheckConstraint(
            "lifecycle_origin IN ('generated', 'carried_forward', 'reinferred')",
            name="lifecycle_origin_supported",
        ),
        sa.CheckConstraint(
            "source_review_status IS NULL OR source_review_status IN ('approved', 'corrected')",
            name="source_review_status_supported",
        ),
        sa.CheckConstraint(
            "source_review_revision IS NULL OR source_review_revision >= 0",
            name="source_review_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "source_effective_content_hash IS NULL OR length(source_effective_content_hash) = 64",
            name="source_effective_hash_length",
        ),
        sa.CheckConstraint(
            "(lifecycle_origin = 'generated' AND "
            "source_semantic_proposal_id IS NULL AND source_knowledge_version_id IS NULL AND "
            "source_review_status IS NULL AND source_review_revision IS NULL AND "
            "source_effective_content_hash IS NULL) OR "
            "(lifecycle_origin IN ('carried_forward', 'reinferred') AND "
            "source_semantic_proposal_id IS NOT NULL AND source_knowledge_version_id IS NOT NULL AND "
            "source_review_status IS NOT NULL AND source_review_revision IS NOT NULL AND "
            "source_effective_content_hash IS NOT NULL)",
            name="lifecycle_lineage_complete",
        ),
        sa.CheckConstraint(
            "source_knowledge_version_id IS NULL OR source_knowledge_version_id <> knowledge_version_id",
            name="source_version_distinct",
        ),
        sa.CheckConstraint(
            "source_semantic_proposal_id IS NULL OR source_semantic_proposal_id <> id",
            name="source_proposal_distinct",
        ),
    ):
        op.create_check_constraint(
            constraint.name,
            "semantic_proposals",
            constraint.sqltext,
        )

    op.create_index(
        "ix_semantic_proposals_source_proposal",
        "semantic_proposals",
        ["source_semantic_proposal_id"],
    )
    op.create_index(
        "ix_semantic_proposals_source_version",
        "semantic_proposals",
        ["source_knowledge_version_id"],
    )

    _replace_immutable_trigger_function(include_lineage=True)


def downgrade():
    _replace_immutable_trigger_function(include_lineage=False)

    op.drop_index(
        "ix_semantic_proposals_source_version",
        table_name="semantic_proposals",
    )
    op.drop_index(
        "ix_semantic_proposals_source_proposal",
        table_name="semantic_proposals",
    )

    for name in (
        "source_proposal_distinct",
        "source_version_distinct",
        "lifecycle_lineage_complete",
        "source_effective_hash_length",
        "source_review_revision_nonnegative",
        "source_review_status_supported",
        "lifecycle_origin_supported",
    ):
        op.drop_constraint(name, "semantic_proposals", type_="check")

    op.drop_column("semantic_proposals", "source_effective_content_hash")
    op.drop_column("semantic_proposals", "source_review_revision")
    op.drop_column("semantic_proposals", "source_review_status")
    op.drop_column("semantic_proposals", "source_knowledge_version_id")
    op.drop_column("semantic_proposals", "source_semantic_proposal_id")
    op.drop_column("semantic_proposals", "lifecycle_origin")
