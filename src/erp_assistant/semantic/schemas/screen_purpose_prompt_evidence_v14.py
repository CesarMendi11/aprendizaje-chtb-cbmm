from __future__ import annotations

from pydantic import ConfigDict

from erp_assistant.structural.canonical.ids import normalize_text

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
from .screen_purpose_inference import InferenceModel

_GENERIC_PROMPT_CONTROL_LABELS = {
    "",
    "unlabeled control",
    "control sin etiqueta",
    "sin etiqueta",
}


def _promptable_control_label(value: str | None) -> bool:
    normalized = normalize_text(value or "")
    return bool(normalized) and normalized not in _GENERIC_PROMPT_CONTROL_LABELS


def _filter_main_content_controls(
    value: str,
    controls: list[ControlEvidence],
) -> str:
    labels = [control.label for control in controls]
    lines: list[str] = []

    for line in value.splitlines():
        prefix, separator, _body = line.partition(":")

        if separator and normalize_text(prefix) == "controles":
            if labels:
                lines.append(f"{prefix}: {'; '.join(labels)}")
            continue

        lines.append(line)

    return "\n".join(lines)


class ScreenPurposePromptEvidenceV14(InferenceModel):
    """Safe evidence exposed to the v14 semantic model.

    Unlike v13, this projection intentionally does not expose an exhaustive
    action whitelist. The model is allowed to interpret the observed structure
    and propose human-reviewable functional claims. Trust remains bounded by
    Safe Evidence, explicit evidence references, mechanical validation, and
    HITL publication.
    """

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

    @classmethod
    def from_package(cls, package):
        read_only_traces = [
            trace for trace in package.network_traces if trace.read_only
        ]
        excluded_network_ids = {
            trace.evidence_id
            for trace in package.network_traces
            if not trace.read_only
        }

        controls = [
            control
            for control in package.controls
            if _promptable_control_label(control.label)
        ]
        visible_control_ids = {control.control_id for control in controls}
        hidden_control_ids = {
            control.control_id
            for control in package.controls
            if control.control_id not in visible_control_ids
        }

        transitions = [
            transition
            if (
                transition.trigger_control_id is None
                or transition.trigger_control_id in visible_control_ids
            )
            else transition.model_copy(update={"trigger_control_id": None})
            for transition in package.transitions
        ]

        return cls(
            screen_id=package.screen_id,
            screen_title=package.screen_title,
            screen_route=package.screen_route,
            module=package.module,
            fields=package.fields,
            controls=controls,
            tables=package.tables,
            ui_states=package.ui_states,
            events=package.events,
            transitions=transitions,
            network_traces=read_only_traces,
            main_content_text=_filter_main_content_controls(
                package.main_content_text,
                controls,
            ),
            evidence_ids=[
                evidence_id
                for evidence_id in package.evidence_ids
                if evidence_id not in excluded_network_ids
                and evidence_id not in hidden_control_ids
            ],
        )
