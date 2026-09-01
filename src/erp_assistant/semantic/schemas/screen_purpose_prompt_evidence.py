from __future__ import annotations

from pydantic import ConfigDict

from .screen_evidence import (
    ControlEvidence,
    EventEvidence,
    FieldEvidence,
    ModuleEvidence,
    NetworkTraceEvidence,
    TableEvidence,
    TransitionEvidence,
    UIStateEvidence,
)
from .screen_purpose_grounding_plan import ScreenPurposeGroundingPlan
from .screen_purpose_inference import InferenceModel


class ScreenPurposePromptEvidence(InferenceModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    screen_id: str
    screen_title: str
    screen_route: str
    module: ModuleEvidence | None
    fields: list[FieldEvidence]
    controls: list[ControlEvidence]
    tables: list[TableEvidence]
    ui_states: list[UIStateEvidence]
    events: list[EventEvidence]
    transitions: list[TransitionEvidence]
    network_traces: list[NetworkTraceEvidence]
    main_content_text: str
    evidence_ids: list[str]
    grounding_plan: ScreenPurposeGroundingPlan

    @classmethod
    def from_package(cls, package):
        from erp_assistant.semantic.validators.screen_purpose_grounding_plan import (
            build_grounding_plan,
        )

        read_only_traces = [trace for trace in package.network_traces if trace.read_only]
        excluded_network_ids = {
            trace.evidence_id for trace in package.network_traces if not trace.read_only
        }
        return cls(
            screen_id=package.screen_id,
            screen_title=package.screen_title,
            screen_route=package.screen_route,
            module=package.module,
            fields=package.fields,
            controls=package.controls,
            tables=package.tables,
            ui_states=package.ui_states,
            events=package.events,
            transitions=package.transitions,
            network_traces=read_only_traces,
            main_content_text=package.main_content_text,
            evidence_ids=[
                evidence_id
                for evidence_id in package.evidence_ids
                if evidence_id not in excluded_network_ids
            ],
            grounding_plan=build_grounding_plan(package),
        )
