from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from erp_assistant.structural.canonical.enums import ReviewStatus

from ..base import Base
from ..enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    RemovalReconciliationDecisionType,
    RemovalReviewActionType,
    ReviewActionType,
    ReviewSource,
    SemanticLifecycleOrigin,
    SemanticType,
    SyncStatus,
    SyncTarget,
)
from ..types import JSONType, StringEnum, new_uuid, utcnow


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class ERPSystemRecord(TimestampMixin, Base):
    __tablename__ = "erp_systems"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    profile_name: Mapped[str] = mapped_column(String(240), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1000))
    adapter: Mapped[str | None] = mapped_column(String(120))
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    versions: Mapped[list["KnowledgeVersionRecord"]] = relationship(back_populates="erp")
    import_runs: Mapped[list["ImportRun"]] = relationship(back_populates="erp")


class ImportRun(TimestampMixin, Base):
    __tablename__ = "import_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    erp_id: Mapped[str] = mapped_column(ForeignKey("erp_systems.id"), index=True)
    source_knowledge_path: Mapped[str] = mapped_column(String(1000))
    source_manifest_path: Mapped[str] = mapped_column(String(1000))
    requested_knowledge_version: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[ImportStatus] = mapped_column(StringEnum(ImportStatus), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inserted_items: Mapped[int] = mapped_column(Integer, default=0)
    carried_reviews: Mapped[int] = mapped_column(Integer, default=0)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    source_hashes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    erp: Mapped[ERPSystemRecord] = relationship(back_populates="import_runs")
    knowledge_version: Mapped["KnowledgeVersionRecord | None"] = relationship(
        back_populates="import_run", uselist=False
    )


class KnowledgeVersionRecord(TimestampMixin, Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint("erp_id", "knowledge_version"),
        Index("ix_knowledge_versions_erp_status", "erp_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    erp_id: Mapped[str] = mapped_column(ForeignKey("erp_systems.id"), index=True)
    import_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_runs.id"), unique=True)
    schema_version: Mapped[str] = mapped_column(String(40))
    knowledge_version: Mapped[str] = mapped_column(String(120))
    canonical_hash: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entity_counts: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_artifact_hashes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    build_warnings: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[KnowledgeVersionStatus] = mapped_column(StringEnum(KnowledgeVersionStatus))
    erp: Mapped[ERPSystemRecord] = relationship(back_populates="versions")
    import_run: Mapped[ImportRun] = relationship(back_populates="knowledge_version")
    items: Mapped[list["KnowledgeItem"]] = relationship(back_populates="knowledge_version")
    semantic_proposals: Mapped[list["SemanticProposal"]] = relationship(
        back_populates="knowledge_version",
        foreign_keys="SemanticProposal.knowledge_version_id",
    )
    sync_jobs: Mapped[list["SyncJob"]] = relationship(back_populates="knowledge_version")
    promotions: Mapped[list["KnowledgeVersionPromotion"]] = relationship(
        back_populates="knowledge_version",
        foreign_keys="KnowledgeVersionPromotion.knowledge_version_id",
    )


class KnowledgeItem(TimestampMixin, Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        UniqueConstraint("knowledge_version_id", "entity_type", "canonical_id"),
        Index("ix_knowledge_items_version_type", "knowledge_version_id", "entity_type"),
        Index("ix_knowledge_items_version_status", "knowledge_version_id", "current_review_status"),
        Index("ix_knowledge_items_route", "route"),
        Index("ix_knowledge_items_canonical_id", "canonical_id"),
        Index("ix_knowledge_items_parent", "parent_canonical_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    knowledge_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id"), nullable=False
    )
    canonical_id: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    parent_canonical_id: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(500))
    normalized_title: Mapped[str | None] = mapped_column(String(500))
    route: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    generated_review_status: Mapped[ReviewStatus] = mapped_column(
        StringEnum(ReviewStatus), nullable=False
    )
    current_review_status: Mapped[ReviewStatus] = mapped_column(
        StringEnum(ReviewStatus), nullable=False
    )
    review_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    knowledge_version: Mapped[KnowledgeVersionRecord] = relationship(back_populates="items")
    review_actions: Mapped[list["ReviewAction"]] = relationship(
        back_populates="knowledge_item",
        order_by="ReviewAction.created_at",
        foreign_keys="ReviewAction.knowledge_item_id",
    )
    semantic_proposals: Mapped[list["SemanticProposal"]] = relationship(
        back_populates="screen_knowledge_item"
    )


class ReviewAction(TimestampMixin, Base):
    __tablename__ = "review_actions"
    __table_args__ = (Index("ix_review_actions_item_created", "knowledge_item_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id"), nullable=False
    )
    previous_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_items.id"))
    action: Mapped[ReviewActionType] = mapped_column(StringEnum(ReviewActionType))
    previous_status: Mapped[ReviewStatus] = mapped_column(StringEnum(ReviewStatus))
    new_status: Mapped[ReviewStatus] = mapped_column(StringEnum(ReviewStatus))
    corrected_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    review_notes: Mapped[str | None] = mapped_column(Text)
    reviewer_subject: Mapped[str | None] = mapped_column(String(240))
    item_content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[ReviewSource] = mapped_column(StringEnum(ReviewSource))
    knowledge_item: Mapped[KnowledgeItem] = relationship(
        back_populates="review_actions", foreign_keys=[knowledge_item_id]
    )


class RemovalReconciliationReviewSet(TimestampMixin, Base):
    __tablename__ = "removal_reconciliation_review_sets"
    __table_args__ = (
        UniqueConstraint(
            "candidate_version_id",
            name="uq_removal_reconciliation_review_sets_candidate",
        ),
        CheckConstraint(
            "candidate_version_id <> active_version_id",
            name="removal_review_versions_distinct",
        ),
        CheckConstraint("length(plan_hash) = 64", name="removal_review_plan_hash_length"),
        CheckConstraint("decision_count >= 0", name="removal_review_decision_count_nonnegative"),
        Index("ix_removal_review_sets_erp_created", "erp_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), nullable=False
    )
    active_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), nullable=False
    )
    erp_id: Mapped[str] = mapped_column(ForeignKey("erp_systems.id", ondelete="RESTRICT"))
    candidate_knowledge_version: Mapped[str] = mapped_column(String(120), nullable=False)
    active_knowledge_version: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_origin: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_diff_totals: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_count: Mapped[int] = mapped_column(Integer, nullable=False)
    decisions: Mapped[list["RemovalReconciliationDecisionRecord"]] = relationship(
        back_populates="review_set",
        order_by="RemovalReconciliationDecisionRecord.entity_type, "
        "RemovalReconciliationDecisionRecord.canonical_id",
    )


class RemovalReconciliationDecisionRecord(TimestampMixin, Base):
    __tablename__ = "removal_reconciliation_decisions"
    __table_args__ = (
        UniqueConstraint(
            "review_set_id",
            "entity_type",
            "canonical_id",
            name="uq_removal_reconciliation_decision_identity",
        ),
        CheckConstraint("review_revision >= 0", name="removal_decision_revision_nonnegative"),
        CheckConstraint(
            "current_decision IS NULL OR current_decision IN "
            "('retain_from_active', 'confirmed_remove')",
            name="removal_current_decision_resolved",
        ),
        CheckConstraint(
            "proposed_decision IN ('retain_from_active', 'confirmed_remove', 'unresolved')",
            name="removal_proposed_decision_supported",
        ),
        CheckConstraint(
            "length(decision_fingerprint) = 64",
            name="removal_decision_fingerprint_length",
        ),
        CheckConstraint(
            "candidate_item_id IS NULL",
            name="removal_decision_candidate_item_absent",
        ),
        Index("ix_removal_decisions_set_current", "review_set_id", "current_decision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    review_set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("removal_reconciliation_review_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    active_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="RESTRICT"), nullable=False
    )
    candidate_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="RESTRICT")
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    canonical_id: Mapped[str] = mapped_column(String(200), nullable=False)
    screen_id: Mapped[str | None] = mapped_column(String(200))
    plan_reason: Mapped[str] = mapped_column(String(240), nullable=False)
    removal_confirmation: Mapped[str | None] = mapped_column(String(60))
    proposed_decision: Mapped[RemovalReconciliationDecisionType] = mapped_column(
        StringEnum(RemovalReconciliationDecisionType), nullable=False
    )
    current_decision: Mapped[RemovalReconciliationDecisionType | None] = mapped_column(
        StringEnum(RemovalReconciliationDecisionType)
    )
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decision_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    review_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    review_set: Mapped[RemovalReconciliationReviewSet] = relationship(back_populates="decisions")
    actions: Mapped[list["RemovalReconciliationReviewAction"]] = relationship(
        back_populates="decision",
        order_by="RemovalReconciliationReviewAction.created_at, "
        "RemovalReconciliationReviewAction.id",
    )


class RemovalReconciliationReviewAction(TimestampMixin, Base):
    __tablename__ = "removal_reconciliation_review_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('confirm_retain', 'confirm_remove', 'reset_to_pending')",
            name="removal_review_action_supported",
        ),
        CheckConstraint(
            "previous_decision IS NULL OR previous_decision IN "
            "('retain_from_active', 'confirmed_remove')",
            name="removal_review_previous_decision_resolved",
        ),
        CheckConstraint(
            "new_decision IS NULL OR new_decision IN ('retain_from_active', 'confirmed_remove')",
            name="removal_review_new_decision_resolved",
        ),
        CheckConstraint(
            "(action = 'confirm_retain' AND previous_decision IS NULL "
            "AND new_decision = 'retain_from_active') OR "
            "(action = 'confirm_remove' AND previous_decision IS NULL "
            "AND new_decision = 'confirmed_remove') OR "
            "(action = 'reset_to_pending' AND previous_decision IS NOT NULL "
            "AND new_decision IS NULL)",
            name="removal_review_action_matches_decision",
        ),
        CheckConstraint(
            "length(decision_fingerprint) = 64",
            name="removal_review_action_fingerprint_length",
        ),
        CheckConstraint(
            "trim(review_notes) <> ''",
            name="removal_review_action_notes_nonempty",
        ),
        CheckConstraint(
            "trim(reviewer_subject) <> ''",
            name="removal_review_action_reviewer_nonempty",
        ),
        Index("ix_removal_review_actions_decision_created", "decision_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("removal_reconciliation_decisions.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[RemovalReviewActionType] = mapped_column(
        StringEnum(RemovalReviewActionType), nullable=False
    )
    previous_decision: Mapped[RemovalReconciliationDecisionType | None] = mapped_column(
        StringEnum(RemovalReconciliationDecisionType)
    )
    new_decision: Mapped[RemovalReconciliationDecisionType | None] = mapped_column(
        StringEnum(RemovalReconciliationDecisionType)
    )
    review_notes: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_subject: Mapped[str] = mapped_column(String(240), nullable=False)
    source: Mapped[ReviewSource] = mapped_column(StringEnum(ReviewSource), nullable=False)
    decision_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[RemovalReconciliationDecisionRecord] = relationship(back_populates="actions")


class SemanticProposal(TimestampMixin, Base):
    __tablename__ = "semantic_proposals"
    __table_args__ = (
        UniqueConstraint("semantic_id", name="uq_semantic_proposals_semantic_id"),
        UniqueConstraint(
            "knowledge_version_id",
            "screen_knowledge_item_id",
            "semantic_type",
            "evidence_hash",
            "prompt_hash",
            "generation_model",
            "generation_parameters_hash",
            name="uq_semantic_proposals_generation_identity",
        ),
        CheckConstraint("trim(semantic_id) <> ''", name="semantic_id_nonempty"),
        CheckConstraint("trim(semantic_type) <> ''", name="semantic_type_nonempty"),
        CheckConstraint("semantic_type = 'screen_purpose'", name="semantic_type_supported"),
        CheckConstraint("trim(generation_model) <> ''", name="generation_model_nonempty"),
        CheckConstraint("trim(prompt_version) <> ''", name="prompt_version_nonempty"),
        CheckConstraint("review_revision >= 0", name="review_revision_nonnegative"),
        CheckConstraint("length(source_content_hash) = 64", name="source_hash_length"),
        CheckConstraint("length(evidence_hash) = 64", name="evidence_hash_length"),
        CheckConstraint("length(prompt_hash) = 64", name="prompt_hash_length"),
        CheckConstraint(
            "length(generation_parameters_hash) = 64",
            name="generation_parameters_hash_length",
        ),
        CheckConstraint(
            "current_review_status IN ('pending_review', 'approved', 'rejected', 'corrected')",
            name="review_status_supported",
        ),
        CheckConstraint(
            "lifecycle_origin IN ('generated', 'carried_forward', 'reinferred')",
            name="lifecycle_origin_supported",
        ),
        CheckConstraint(
            "source_review_status IS NULL OR source_review_status IN ('approved', 'corrected')",
            name="source_review_status_supported",
        ),
        CheckConstraint(
            "source_review_revision IS NULL OR source_review_revision >= 0",
            name="source_review_revision_nonnegative",
        ),
        CheckConstraint(
            "source_effective_content_hash IS NULL OR length(source_effective_content_hash) = 64",
            name="source_effective_hash_length",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "source_knowledge_version_id IS NULL OR source_knowledge_version_id <> knowledge_version_id",
            name="source_version_distinct",
        ),
        CheckConstraint(
            "source_semantic_proposal_id IS NULL OR source_semantic_proposal_id <> id",
            name="source_proposal_distinct",
        ),
        Index(
            "ix_semantic_proposals_version_status",
            "knowledge_version_id",
            "current_review_status",
        ),
        Index(
            "ix_semantic_proposals_screen_type",
            "screen_knowledge_item_id",
            "semantic_type",
        ),
        Index(
            "ix_semantic_proposals_version_type_status",
            "knowledge_version_id",
            "semantic_type",
            "current_review_status",
        ),
        Index("ix_semantic_proposals_evidence_hash", "evidence_hash"),
        Index("ix_semantic_proposals_source_proposal", "source_semantic_proposal_id"),
        Index("ix_semantic_proposals_source_version", "source_knowledge_version_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    semantic_id: Mapped[str] = mapped_column(String(240), nullable=False)
    knowledge_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT"), nullable=False
    )
    screen_knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="RESTRICT"), nullable=False
    )
    semantic_type: Mapped[SemanticType] = mapped_column(StringEnum(SemanticType), nullable=False)
    source_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSONType, nullable=False)
    generation_model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONType, default=dict, nullable=False
    )
    generation_parameters_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_origin: Mapped[SemanticLifecycleOrigin] = mapped_column(
        StringEnum(SemanticLifecycleOrigin),
        default=SemanticLifecycleOrigin.GENERATED,
        nullable=False,
    )
    source_semantic_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("semantic_proposals.id", ondelete="RESTRICT")
    )
    source_knowledge_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT")
    )
    source_review_status: Mapped[ReviewStatus | None] = mapped_column(StringEnum(ReviewStatus))
    source_review_revision: Mapped[int | None] = mapped_column(Integer)
    source_effective_content_hash: Mapped[str | None] = mapped_column(String(64))
    current_review_status: Mapped[ReviewStatus] = mapped_column(
        StringEnum(ReviewStatus), default=ReviewStatus.PENDING_REVIEW, nullable=False
    )
    review_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    knowledge_version: Mapped[KnowledgeVersionRecord] = relationship(
        back_populates="semantic_proposals",
        foreign_keys=[knowledge_version_id],
    )
    screen_knowledge_item: Mapped[KnowledgeItem] = relationship(back_populates="semantic_proposals")
    review_actions: Mapped[list["SemanticReviewAction"]] = relationship(
        back_populates="semantic_proposal",
        order_by="SemanticReviewAction.created_at",
    )


class SemanticReviewAction(TimestampMixin, Base):
    __tablename__ = "semantic_review_actions"
    __table_args__ = (
        CheckConstraint("trim(reviewer_subject) <> ''", name="reviewer_subject_nonempty"),
        CheckConstraint("trim(source) <> ''", name="source_nonempty"),
        CheckConstraint("length(proposal_content_hash) = 64", name="proposal_hash_length"),
        CheckConstraint(
            "action IN ('approve', 'reject', 'correct', 'reset_to_pending')",
            name="action_supported",
        ),
        CheckConstraint(
            "previous_status IN ('pending_review', 'approved', 'rejected', 'corrected')",
            name="previous_status_supported",
        ),
        CheckConstraint(
            "new_status IN ('pending_review', 'approved', 'rejected', 'corrected')",
            name="new_status_supported",
        ),
        Index(
            "ix_semantic_review_actions_proposal_created",
            "semantic_proposal_id",
            "created_at",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    semantic_proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("semantic_proposals.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[ReviewActionType] = mapped_column(StringEnum(ReviewActionType), nullable=False)
    previous_status: Mapped[ReviewStatus] = mapped_column(StringEnum(ReviewStatus), nullable=False)
    new_status: Mapped[ReviewStatus] = mapped_column(StringEnum(ReviewStatus), nullable=False)
    corrected_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    review_notes: Mapped[str | None] = mapped_column(String(4000))
    reviewer_subject: Mapped[str] = mapped_column(String(240), nullable=False)
    proposal_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    semantic_proposal: Mapped[SemanticProposal] = relationship(back_populates="review_actions")


class KnowledgeVersionPromotion(TimestampMixin, Base):
    __tablename__ = "knowledge_version_promotions"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_version_id",
            name="uq_knowledge_version_promotions_version",
        ),
        Index(
            "ix_knowledge_version_promotions_created_at",
            "created_at",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    knowledge_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    previous_active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="RESTRICT")
    )
    reviewer_subject: Mapped[str] = mapped_column(String(240), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    gate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    knowledge_version: Mapped[KnowledgeVersionRecord] = relationship(
        back_populates="promotions",
        foreign_keys=[knowledge_version_id],
    )


@event.listens_for(KnowledgeVersionPromotion, "before_update")
@event.listens_for(KnowledgeVersionPromotion, "before_delete")
def _immutable_knowledge_version_promotions(*_: Any) -> None:
    raise ValueError("knowledge_version_promotions es un historial inmutable")


class PipelineJob(TimestampMixin, Base):
    __tablename__ = "pipeline_jobs"
    __table_args__ = (
        Index("ix_pipeline_jobs_status_kind", "status", "kind"),
        Index("ix_pipeline_jobs_requested_at", "requested_at"),
        CheckConstraint("progress_current >= 0", name="progress_current_nonnegative"),
        CheckConstraint(
            "progress_total IS NULL OR progress_total >= 0",
            name="progress_total_nonnegative",
        ),
        CheckConstraint(
            "progress_total IS NULL OR progress_current <= progress_total",
            name="progress_within_total",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    kind: Mapped[PipelineJobKind] = mapped_column(StringEnum(PipelineJobKind), nullable=False)
    status: Mapped[PipelineJobStatus] = mapped_column(
        StringEnum(PipelineJobStatus), default=PipelineJobStatus.QUEUED, nullable=False
    )
    scope: Mapped[PipelineJobScope] = mapped_column(StringEnum(PipelineJobScope), nullable=False)
    target: Mapped[str | None] = mapped_column(String(1000))
    profile_name: Mapped[str | None] = mapped_column(String(240))
    erp_id: Mapped[str | None] = mapped_column(ForeignKey("erp_systems.id"), index=True)
    knowledge_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_versions.id"), index=True
    )
    request_source: Mapped[str] = mapped_column(String(60), default="admin_api", nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    stage: Mapped[str] = mapped_column(String(120), default="queued", nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict, nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SyncJob(TimestampMixin, Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        UniqueConstraint("knowledge_version_id", "target"),
        CheckConstraint("attempt_count >= 0", name="sync_attempt_nonnegative"),
        Index("ix_sync_jobs_status_target", "status", "target"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    knowledge_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_versions.id"), nullable=False
    )
    target: Mapped[SyncTarget] = mapped_column(StringEnum(SyncTarget))
    status: Mapped[SyncStatus] = mapped_column(StringEnum(SyncStatus))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    knowledge_version: Mapped[KnowledgeVersionRecord] = relationship(back_populates="sync_jobs")


@event.listens_for(ReviewAction, "before_update")
@event.listens_for(ReviewAction, "before_delete")
def _immutable_review_actions(*_: Any) -> None:
    raise ValueError("review_actions es un historial inmutable")


@event.listens_for(RemovalReconciliationReviewSet, "before_update")
@event.listens_for(RemovalReconciliationReviewSet, "before_delete")
def _immutable_removal_review_set(*_: Any) -> None:
    raise ValueError("removal_reconciliation_review_sets es inmutable")


@event.listens_for(RemovalReconciliationReviewAction, "before_update")
@event.listens_for(RemovalReconciliationReviewAction, "before_delete")
def _immutable_removal_review_actions(*_: Any) -> None:
    raise ValueError("removal_reconciliation_review_actions es un historial inmutable")


@event.listens_for(RemovalReconciliationDecisionRecord, "before_update")
def _immutable_removal_decision(_mapper: Any, _connection: Any, target) -> None:
    state = inspect(target)
    mutable = {"current_decision", "review_revision", "updated_at"}
    changed = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if attribute.key not in mutable and state.attrs[attribute.key].history.has_changes()
    }
    if changed:
        raise ValueError("La identidad y procedencia de removal decision son inmutables")


@event.listens_for(RemovalReconciliationDecisionRecord, "before_delete")
def _prevent_removal_decision_delete(*_: Any) -> None:
    raise ValueError("RemovalReconciliationDecisionRecord no puede eliminarse")


@event.listens_for(SemanticReviewAction, "before_update")
@event.listens_for(SemanticReviewAction, "before_delete")
def _immutable_semantic_review_actions(*_: Any) -> None:
    raise ValueError("semantic_review_actions es un historial inmutable")


@event.listens_for(SemanticProposal, "before_update")
def _immutable_semantic_proposal(_mapper: Any, _connection: Any, target: SemanticProposal) -> None:
    state = inspect(target)
    mutable = {"current_review_status", "review_revision", "updated_at"}
    changed = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if attribute.key not in mutable and state.attrs[attribute.key].history.has_changes()
    }
    if changed:
        raise ValueError("La identidad y procedencia de SemanticProposal son inmutables")


@event.listens_for(SemanticProposal, "before_delete")
def _prevent_semantic_proposal_delete(*_: Any) -> None:
    raise ValueError("SemanticProposal no puede eliminarse")


@event.listens_for(KnowledgeItem, "before_update")
def _immutable_source_payload(_mapper: Any, _connection: Any, target: KnowledgeItem) -> None:
    if inspect(target).attrs.source_payload.history.has_changes():
        raise ValueError("source_payload es inmutable después de importar")


@event.listens_for(KnowledgeVersionRecord, "before_update")
def _immutable_knowledge_version(
    _mapper: Any, _connection: Any, target: KnowledgeVersionRecord
) -> None:
    state = inspect(target)
    mutable = {"status"}
    changed = {
        attribute.key
        for attribute in state.mapper.column_attrs
        if attribute.key not in mutable and state.attrs[attribute.key].history.has_changes()
    }
    if changed:
        raise ValueError("Una versión importada es inmutable")


__all__ = [
    "ERPSystemRecord",
    "ImportRun",
    "KnowledgeVersionRecord",
    "KnowledgeItem",
    "ReviewAction",
    "RemovalReconciliationReviewSet",
    "RemovalReconciliationDecisionRecord",
    "RemovalReconciliationReviewAction",
    "SemanticProposal",
    "SemanticReviewAction",
    "KnowledgeVersionPromotion",
    "PipelineJob",
    "SyncJob",
]
