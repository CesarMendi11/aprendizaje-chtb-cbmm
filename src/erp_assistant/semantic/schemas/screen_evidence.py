from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class NetworkTraceEvidence(EvidenceModel):
    evidence_id: str
    methods: tuple[str, ...] = ()
    endpoint_paths: tuple[str, ...] = ()
    resource_types: tuple[str, ...] = ()
    origin_kinds: tuple[str, ...] = ()
    status_codes: tuple[int, ...] = ()
    query_keys: tuple[str, ...] = ()
    observation_count: int = Field(ge=1)
    endpoint_count: int = Field(ge=1)
    read_only: bool


class ScreenEvidencePackage(EvidenceModel):
    schema_version: Literal["1.1"] = "1.1"
    erp_id: str
    knowledge_version_id: uuid.UUID
    knowledge_version: str
    screen_id: str
    screen_title: str
    screen_route: str
    module: ModuleEvidence | None
    fields: list[FieldEvidence] = Field(default_factory=list)
    controls: list[ControlEvidence] = Field(default_factory=list)
    tables: list[TableEvidence] = Field(default_factory=list)
    ui_states: list[UIStateEvidence] = Field(default_factory=list)
    events: list[EventEvidence] = Field(default_factory=list)
    transitions: list[TransitionEvidence] = Field(default_factory=list)
    network_traces: list[NetworkTraceEvidence] = Field(default_factory=list)
    main_content_text: str
    primary_evidence_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_network_trace_references(self):
        evidence_ids = set(self.evidence_ids)
        if any(trace.evidence_id not in evidence_ids for trace in self.network_traces):
            raise ValueError("network_traces contiene una referencia fuera de evidence_ids")
        if len({trace.evidence_id for trace in self.network_traces}) != len(self.network_traces):
            raise ValueError("network_traces contiene referencias duplicadas")
        return self

    @model_validator(mode="after")
    def validate_transition_state_references(self):
        state_ids = {state.state_id for state in self.ui_states}
        for transition in self.transitions:
            if (
                transition.source_state_id is not None
                and transition.source_state_id not in state_ids
            ):
                raise ValueError("transitions contiene source_state_id fuera de ui_states")
            if (
                transition.target_state_id is not None
                and transition.target_state_id not in state_ids
            ):
                raise ValueError("transitions contiene target_state_id fuera de ui_states")
        return self
