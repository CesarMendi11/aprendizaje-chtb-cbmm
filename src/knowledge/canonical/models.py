from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import ControlType, EvidenceType, IssueSeverity, ReviewStatus


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Reviewable(CanonicalModel):
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_notes: str | None = None


class ERPSystem(CanonicalModel):
    id: str
    slug: str
    name: str
    profile_name: str
    base_url: str | None = None
    adapter: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Module(Reviewable):
    id: str
    erp_id: str
    name: str
    normalized_name: str
    route_prefix: str | None = None
    description: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Screen(Reviewable):
    id: str
    erp_id: str
    module_id: str | None = None
    title: str
    normalized_title: str
    route: str
    document_title: str | None = None
    title_source: str | None = None
    main_content_text: str = ""
    description: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UIState(Reviewable):
    id: str
    screen_id: str
    route: str
    depth: int = 0
    title: str
    exact_fingerprint: str | None = None
    structural_fingerprint: str
    is_route_root: bool = False
    observed_path: list[dict[str, Any]] = Field(default_factory=list)
    restore_path: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FieldEntity(Reviewable):
    id: str
    screen_id: str
    label: str
    normalized_label: str
    name: str | None = None
    input_type: str | None = None
    placeholder: str | None = None
    required: bool = False
    readonly: bool = False
    disabled: bool = False
    region: str = "main_content"
    selector: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Control(Reviewable):
    id: str
    screen_id: str
    label: str
    normalized_label: str
    control_type: ControlType
    event_category: str | None = None
    safety_decision: str | None = None
    mutative: bool = False
    region: str = "main_content"
    selector: str | None = None
    target_route: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Table(Reviewable):
    id: str
    screen_id: str
    name: str | None = None
    normalized_name: str | None = None
    region: str = "main_content"
    column_ids: list[str] = Field(default_factory=list)
    row_count_observed: int | None = None
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class TableColumn(Reviewable):
    id: str
    table_id: str
    name: str
    normalized_name: str
    position: int
    source_refs: list[str] = Field(default_factory=list)


class Link(Reviewable):
    id: str
    screen_id: str
    label: str
    normalized_label: str
    target_route: str
    target_screen_id: str | None = None
    region: str = "main_content"
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Event(Reviewable):
    id: str
    screen_id: str
    source_state_id: str | None = None
    label: str
    normalized_label: str
    category: str
    policy_decision: str
    mutative: bool = False
    selector: str | None = None
    region: str = "unknown"
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Transition(Reviewable):
    id: str
    source_state_id: str
    target_state_id: str
    event_id: str | None = None
    category: str
    changed: bool
    route_changed: bool
    restore_strategy: str | None = None
    depth: int = 0
    observed: bool = True
    source_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class Evidence(CanonicalModel):
    id: str
    evidence_type: EvidenceType
    artifact_path: str
    artifact_hash: str | None = None
    source_entity_type: str
    source_entity_id: str
    observed_text: str | None = None
    selector: str | None = None
    captured_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BuildWarning(CanonicalModel):
    code: str
    message: str
    entity_type: str | None = None
    entity_id: str | None = None
    count: int = 1


class ValidationIssue(CanonicalModel):
    severity: IssueSeverity
    code: str
    message: str
    entity_type: str | None = None
    entity_id: str | None = None


class CanonicalKnowledgeBase(CanonicalModel):
    schema_version: str
    knowledge_version: str
    generated_at: datetime
    generator_version: str | None = None
    source_profile: str
    source_artifacts: list[str]
    source_artifact_hashes: dict[str, str]
    erp_system: ERPSystem
    modules: list[Module] = Field(default_factory=list)
    screens: list[Screen] = Field(default_factory=list)
    ui_states: list[UIState] = Field(default_factory=list)
    fields: list[FieldEntity] = Field(default_factory=list)
    controls: list[Control] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    table_columns: list[TableColumn] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    build_warnings: list[BuildWarning] = Field(default_factory=list)
    statistics: dict[str, int] = Field(default_factory=dict)


Field = FieldEntity
