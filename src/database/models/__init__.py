from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
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

from src.knowledge.canonical.enums import ReviewStatus

from ..base import Base
from ..enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    ReviewActionType,
    ReviewSource,
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
    import_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("import_runs.id"), unique=True
    )
    schema_version: Mapped[str] = mapped_column(String(40))
    knowledge_version: Mapped[str] = mapped_column(String(120))
    canonical_hash: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entity_counts: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    source_artifact_hashes: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    build_warnings: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[KnowledgeVersionStatus] = mapped_column(
        StringEnum(KnowledgeVersionStatus)
    )
    erp: Mapped[ERPSystemRecord] = relationship(back_populates="versions")
    import_run: Mapped[ImportRun] = relationship(back_populates="knowledge_version")
    items: Mapped[list["KnowledgeItem"]] = relationship(back_populates="knowledge_version")
    sync_jobs: Mapped[list["SyncJob"]] = relationship(back_populates="knowledge_version")


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


class ReviewAction(TimestampMixin, Base):
    __tablename__ = "review_actions"
    __table_args__ = (Index("ix_review_actions_item_created", "knowledge_item_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_items.id"), nullable=False
    )
    previous_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_items.id")
    )
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
    "SyncJob",
]
