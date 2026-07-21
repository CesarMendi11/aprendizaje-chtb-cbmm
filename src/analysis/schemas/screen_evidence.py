from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModuleEvidence(EvidenceModel):
    module_id: str
    name: str


class FieldEvidence(EvidenceModel):
    field_id: str
    label: str
    input_type: str | None = None
    required: bool
    readonly: bool


class ControlEvidence(EvidenceModel):
    control_id: str
    label: str
    control_type: str | None = None
    mutative: bool
    safety_decision: str | None = None


class ColumnEvidence(EvidenceModel):
    column_id: str
    label: str


class TableEvidence(EvidenceModel):
    table_id: str
    name: str
    columns: list[ColumnEvidence] = Field(default_factory=list)


class UIStateEvidence(EvidenceModel):
    state_id: str
    title: str
    depth: int | None = None


class EventEvidence(EvidenceModel):
    event_id: str
    label: str
    category: str
    policy_decision: str
    mutative: bool


class TransitionEvidence(EvidenceModel):
    transition_id: str
    category: str
    source_state_id: str | None = None
    target_state_id: str | None = None
    trigger_control_id: str | None = None


class ScreenEvidencePackage(EvidenceModel):
    schema_version: Literal["1.0"] = "1.0"
    erp_id: str
    knowledge_version_id: uuid.UUID
    knowledge_version: str
    screen_id: str
    screen_title: str
    screen_route: str
    module: ModuleEvidence
    fields: list[FieldEvidence] = Field(default_factory=list)
    controls: list[ControlEvidence] = Field(default_factory=list)
    tables: list[TableEvidence] = Field(default_factory=list)
    ui_states: list[UIStateEvidence] = Field(default_factory=list)
    events: list[EventEvidence] = Field(default_factory=list)
    transitions: list[TransitionEvidence] = Field(default_factory=list)
    main_content_text: str
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
