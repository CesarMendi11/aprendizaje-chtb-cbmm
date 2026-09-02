from __future__ import annotations

from typing import Any

from erp_assistant.semantic.generation.errors import InferenceGroundingError
from erp_assistant.semantic.schemas import ScreenEvidencePackage, ScreenPurposeInference
from erp_assistant.structural.canonical.ids import normalize_text

_GENERIC_LABELS = {
    "",
    "unlabeled control",
    "control sin etiqueta",
    "sin etiqueta",
}
_NONFUNCTIONAL_EVENT_CATEGORIES = {"expand menu"}


def _meaningful_label(value: str | None) -> bool:
    normalized = normalize_text(value or "")
    return normalized not in _GENERIC_LABELS and bool(normalized)


def _is_nonfunctional_shell_category(value: str | None) -> bool:
    return normalize_text(value or "") in _NONFUNCTIONAL_EVENT_CATEGORIES


def claimable_reference_index(package: ScreenEvidencePackage) -> dict[str, dict[str, Any]]:
    """References that may directly support an LLM-proposed functional claim.

    The policy constrains provenance, not semantics. It deliberately avoids an
    action vocabulary: humans decide whether a proposed interpretation is
    functionally correct. Screen/module identity, generic evidence records,
    network traces, unlabeled controls, and global menu expansion are not
    sufficient direct claim evidence.
    """

    index: dict[str, dict[str, Any]] = {}

    for field in package.fields:
        if _meaningful_label(field.label):
            index[field.field_id] = {"type": "field", "label": field.label}

    for control in package.controls:
        if _meaningful_label(control.label):
            index[control.control_id] = {
                "type": "control",
                "label": control.label,
                "mutative": control.mutative,
                "decision": control.safety_decision,
            }

    for table in package.tables:
        if _meaningful_label(table.name):
            index[table.table_id] = {"type": "table", "label": table.name}
        for column in table.columns:
            if _meaningful_label(column.label):
                index[column.column_id] = {
                    "type": "column",
                    "label": column.label,
                    "table_id": table.table_id,
                }

    screen_title = normalize_text(package.screen_title)
    for state in package.ui_states:
        state_title = normalize_text(state.title)
        if state_title and state_title != screen_title:
            index[state.state_id] = {"type": "state", "label": state.title}

    for event in package.events:
        if _is_nonfunctional_shell_category(event.category):
            continue
        if _meaningful_label(event.label):
            index[event.event_id] = {
                "type": "event",
                "label": event.label,
                "category": event.category,
                "mutative": event.mutative,
                "decision": event.policy_decision,
            }

    for transition in package.transitions:
        if _is_nonfunctional_shell_category(transition.category):
            continue
        if normalize_text(transition.category or ""):
            index[transition.transition_id] = {
                "type": "transition",
                "label": transition.category,
                "trigger_control_id": transition.trigger_control_id,
            }

    return index


def claimable_reference_ids(package: ScreenEvidencePackage) -> tuple[str, ...]:
    return tuple(sorted(claimable_reference_index(package)))


def meaningful_semantic_signal_count(package: ScreenEvidencePackage) -> int:
    return len(claimable_reference_index(package))


def validate_v14_claim_references(
    inference: ScreenPurposeInference,
    package: ScreenEvidencePackage,
) -> None:
    """Mechanically validate provenance without deciding semantic truth."""

    allowed = set(claimable_reference_ids(package))
    normalized_statements: set[str] = set()
    for position, claim in enumerate(inference.supported_capabilities):
        statement_key = normalize_text(claim.statement)
        if statement_key in normalized_statements:
            raise InferenceGroundingError(
                "La inferencia contiene afirmaciones funcionales duplicadas",
                stage="claim_reference_validation",
                location=("supported_capabilities", position, "statement"),
                category="duplicate_functional_claim",
            )
        normalized_statements.add(statement_key)

        unknown = set(claim.evidence_refs) - allowed
        if unknown:
            raise InferenceGroundingError(
                "La inferencia usa referencias no admitidas como soporte funcional directo",
                stage="claim_reference_validation",
                location=("supported_capabilities", position, "evidence_refs"),
                category="non_claimable_reference",
            )
