from __future__ import annotations

import re
from enum import IntEnum
from typing import Any

from src.analysis.generation.errors import (
    InferenceGroundingError,
    InferenceNarrativeQualityError,
    InferencePurposeGroundingError,
    InferenceUnsupportedActionError,
)
from src.analysis.schemas import ScreenEvidencePackage, ScreenPurposeInference
from src.analysis.schemas.screen_purpose_grounding_plan import ScreenPurposeGroundingPlan
from src.knowledge.canonical.ids import normalize_text

from .screen_purpose_grounding_plan import build_grounding_plan

CANONICAL_ID = re.compile(
    r"(?<!\w)(?:screen|module|field|control|table|column|state|event|transition|evidence):"
    r"[A-Za-z0-9._-]+",
    re.I,
)
STOPWORDS = {
    "a",
    "al",
    "con",
    "de",
    "del",
    "el",
    "en",
    "es",
    "esta",
    "la",
    "las",
    "lo",
    "los",
    "para",
    "permite",
    "por",
    "que",
    "se",
    "su",
    "una",
    "un",
    "y",
    "interfaz",
    "opcion",
    "control",
    "asociado",
    "asociada",
    "presenta",
    "existe",
}
ACTION_WORDS = {
    "search": {"buscar", "busqueda", "consultar", "consulta", "filtrar", "filtro", "criterio"},
    "navigate": {
        "navegar",
        "navegacion",
        "pagina",
        "paginar",
        "siguiente",
        "anterior",
        "primera",
        "ultima",
    },
    "create": {"crear", "creacion", "nuevo", "nueva", "registrar", "registro", "guardar"},
    "edit": {"editar", "edicion", "modificar", "modificacion"},
    "delete": {"eliminar", "eliminacion", "borrar"},
    "view": {"visualizar", "visualiza", "mostrar", "listar", "lista", "ver"},
    "process": {"procesar", "procesamiento"},
    "manage": {"gestionar", "administrar"},
}
MUTATIVE_ACTIONS = {"create", "edit", "delete", "process"}
PRUDENT_PHRASES = (
    "presenta una opcion",
    "muestra una opcion",
    "existe un control",
    "opcion asociada",
    "control asociado",
    "relacionada con",
    "relacionado con",
)
ABSOLUTE_NEGATIVE_PHRASES = (
    "no se puede",
    "no puede",
    "no permite",
    "es imposible",
    "carece de",
)
EPISTEMIC_NEGATIVE_PHRASES = (
    "la evidencia disponible no permite confirmar",
    "la evidencia no permite confirmar",
    "la estructura observada no demuestra",
    "la estructura no demuestra",
    "no se identificaron controles",
    "no hay evidencia estructural",
)


class ActionSupport(IntEnum):
    PRUDENT_ONLY = 1
    DIRECT = 2


def build_reference_index(package: ScreenEvidencePackage) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {
        package.screen_id: {"type": "screen", "label": package.screen_title},
        package.module.module_id: {"type": "module", "label": package.module.name},
    }
    for field in package.fields:
        index[field.field_id] = {
            "type": "field",
            "label": field.label,
            "input_type": field.input_type,
            "required": field.required,
            "readonly": field.readonly,
        }
    for control in package.controls:
        index[control.control_id] = {
            "type": "control",
            "label": control.label,
            "control_type": control.control_type,
            "mutative": control.mutative,
            "decision": control.safety_decision,
        }
    for table in package.tables:
        index[table.table_id] = {"type": "table", "label": table.name}
        for column in table.columns:
            index[column.column_id] = {
                "type": "column",
                "label": column.label,
                "table_id": table.table_id,
            }
    for state in package.ui_states:
        index[state.state_id] = {"type": "state", "label": state.title}
    for event in package.events:
        index[event.event_id] = {
            "type": "event",
            "label": event.label,
            "category": event.category,
            "decision": event.policy_decision,
            "mutative": event.mutative,
        }
    for transition in package.transitions:
        index[transition.transition_id] = {
            "type": "transition",
            "label": transition.category,
            "source_state_id": transition.source_state_id,
            "target_state_id": transition.target_state_id,
            "trigger_control_id": transition.trigger_control_id,
        }
    for evidence_id in package.evidence_ids:
        index[evidence_id] = {"type": "evidence", "label": ""}
    return index


def _tokens(value: str) -> set[str]:
    return {token for token in normalize_text(value).split() if token and token not in STOPWORDS}


def _actions(tokens: set[str]) -> set[str]:
    return {name for name, words in ACTION_WORDS.items() if tokens & words}


def _diagnostic(error, location, category, value):
    raise error(
        "La inferencia no está respaldada por la evidencia estructural",
        stage="grounding_validation",
        location=location,
        category=category,
        value_length=len(value),
        value_type=type(value).__name__,
    )


def _validate_narrative(value: str, location: tuple[Any, ...], minimum_words: int) -> None:
    if CANONICAL_ID.search(value):
        _diagnostic(InferenceNarrativeQualityError, location, "canonical_id_in_narrative", value)
    if len(normalize_text(value).split()) < minimum_words:
        _diagnostic(
            InferenceNarrativeQualityError, location, "insufficient_natural_language", value
        )


def validate_capability_grounding(
    inference: ScreenPurposeInference,
    package: ScreenEvidencePackage,
    plan: ScreenPurposeGroundingPlan | None = None,
) -> dict[str, ActionSupport]:
    index = build_reference_index(package)
    grounding_plan = plan or build_grounding_plan(package)
    plan_hints = {hint.action: hint for hint in grounding_plan.supported_actions}
    capability_support: dict[str, ActionSupport] = {}
    for position, claim in enumerate(inference.supported_capabilities):
        location = ("supported_capabilities", position, "statement")
        _validate_narrative(claim.statement, location, 3)
        all_statement_tokens = set(normalize_text(claim.statement).split())
        statement_tokens = _tokens(claim.statement)
        refs = [index[reference] for reference in claim.evidence_refs]
        actions = _actions(all_statement_tokens)
        if _describes_view(all_statement_tokens, refs):
            actions.add("view")
        if "manage" in actions:
            _diagnostic(
                InferenceGroundingError, location, "generic_management_action", claim.statement
            )
        planned = actions & set(ACTION_WORDS) - {"manage"}
        unsupported_by_plan = planned - set(plan_hints)
        if unsupported_by_plan:
            category = "unsupported_action:" + ",".join(sorted(unsupported_by_plan))
            _diagnostic(InferenceUnsupportedActionError, location, category, claim.statement)
        if planned:
            permitted_refs = set().union(
                *(set(plan_hints[action].evidence_refs) for action in planned)
            )
            supplied_refs = set(claim.evidence_refs)
            if not supplied_refs.issubset(permitted_refs) or any(
                supplied_refs.isdisjoint(plan_hints[action].evidence_refs) for action in planned
            ):
                _diagnostic(
                    InferenceGroundingError,
                    location,
                    "action_reference_not_permitted",
                    claim.statement,
                )
        reference_tokens = set()
        for reference in refs:
            reference_tokens |= _tokens(
                " ".join(str(reference.get(key) or "") for key in ("label", "category", "type"))
            )
        supported_actions = _supported_actions(refs, reference_tokens)
        unsupported = actions - supported_actions
        if unsupported:
            error = (
                InferenceUnsupportedActionError
                if unsupported & MUTATIVE_ACTIONS
                else InferenceGroundingError
            )
            category = "unsupported_action:" + ",".join(sorted(unsupported))
            _diagnostic(error, location, category, claim.statement)
        for action in actions:
            level = (
                _validate_mutative(action, claim.statement, refs, location)
                if action in MUTATIVE_ACTIONS
                else ActionSupport.DIRECT
            )
            capability_support[action] = max(capability_support.get(action, level), level)
        meaningful = statement_tokens - set().union(*ACTION_WORDS.values())
        if not actions and not meaningful.intersection(reference_tokens):
            _diagnostic(
                InferenceGroundingError,
                location,
                "semantically_irrelevant_reference",
                claim.statement,
            )

    for position, value in enumerate(inference.limitations):
        location = ("limitations", position)
        _validate_narrative(value, location, 2)
        _validate_epistemic_negative(value, location)
    for position, value in enumerate(inference.uncertainties):
        location = ("uncertainties", position)
        _validate_narrative(value, location, 2)
        _validate_epistemic_negative(value, location)
    _validate_purpose(
        inference.purpose_summary,
        capability_support,
        package,
        set(grounding_plan.forbidden_actions),
    )
    return capability_support


def _describes_view(tokens, refs):
    reference_types = {ref["type"] for ref in refs}
    if not reference_types.intersection({"screen", "table", "column"}):
        return False
    if tokens.intersection({"muestra", "visualiza", "lista"}):
        return True
    return "presenta" in tokens and "tabla" in tokens


def _validate_epistemic_negative(value, location):
    normalized = normalize_text(value)
    actions = _actions(set(normalized.split()))
    if not actions or any(phrase in normalized for phrase in EPISTEMIC_NEGATIVE_PHRASES):
        return
    if any(phrase in normalized for phrase in ABSOLUTE_NEGATIVE_PHRASES):
        _diagnostic(
            InferenceGroundingError,
            location,
            "unsupported_absolute_negative_claim",
            value,
        )


def _supported_actions(refs, tokens):
    supported = set()
    if tokens & ACTION_WORDS["search"]:
        supported.update({"search", "view"})
    if tokens & ACTION_WORDS["navigate"]:
        supported.add("navigate")
    if any(ref["type"] in {"screen", "table", "field", "column"} for ref in refs):
        supported.add("view")
    for action in MUTATIVE_ACTIONS:
        if tokens & ACTION_WORDS[action]:
            supported.add(action)
    return supported


def _validate_mutative(action, statement, refs, location):
    relevant = [ref for ref in refs if ref["type"] in {"control", "event"}]
    if not relevant or not any(ref.get("mutative") for ref in relevant):
        _diagnostic(
            InferenceUnsupportedActionError, location, "missing_mutative_evidence", statement
        )
    decisions = {str(ref.get("decision") or "unknown").casefold() for ref in relevant}
    if "deny" in decisions:
        _diagnostic(InferenceUnsupportedActionError, location, "mutative_policy_denied", statement)
    if "allow" in decisions:
        return ActionSupport.DIRECT
    if not _is_prudent(statement):
        _diagnostic(
            InferenceUnsupportedActionError, location, "mutative_wording_not_prudent", statement
        )
    return ActionSupport.PRUDENT_ONLY


def _is_prudent(value):
    normalized = normalize_text(value)
    return any(phrase in normalized for phrase in PRUDENT_PHRASES)


def validate_declared_capability(capability, hint, *, position: int) -> None:
    """Validate that a draft statement describes exactly its declared action."""
    location = ("supported_capabilities", position, "statement")
    _validate_narrative(capability.statement, location, 3)
    tokens = set(normalize_text(capability.statement).split())
    actions = _actions(tokens) - {"manage"}
    if capability.action == "view" and tokens.intersection(
        {"muestra", "visualiza", "lista", "presenta"}
    ):
        actions.add("view")
    if actions != {capability.action}:
        _diagnostic(
            InferenceUnsupportedActionError,
            location,
            "declared_action_statement_mismatch",
            capability.statement,
        )
    if hint.narrative_rule == "prudent_only" and not _is_prudent(capability.statement):
        _diagnostic(
            InferenceUnsupportedActionError,
            location,
            "declared_action_requires_prudent_wording",
            capability.statement,
        )


def _validate_purpose(summary, capability_support, package, forbidden_actions):
    location = ("purpose_summary",)
    _validate_narrative(summary, location, 4)
    tokens = _tokens(summary)
    actions = _actions(tokens)
    if "manage" in actions:
        _diagnostic(InferencePurposeGroundingError, location, "generic_management_purpose", summary)
    forbidden = actions & forbidden_actions
    if forbidden:
        _diagnostic(
            InferencePurposeGroundingError,
            location,
            "forbidden_action:" + ",".join(sorted(forbidden)),
            summary,
        )
    allowed = set(capability_support)
    if "search" in allowed:
        allowed.add("view")
    if actions - allowed:
        _diagnostic(
            InferencePurposeGroundingError, location, "purpose_exceeds_capabilities", summary
        )
    for action in actions & MUTATIVE_ACTIONS:
        if capability_support[action] == ActionSupport.PRUDENT_ONLY and not _is_prudent(summary):
            _diagnostic(
                InferencePurposeGroundingError,
                location,
                "purpose_mutative_wording_not_prudent",
                summary,
            )
    object_tokens = _tokens(package.screen_title) | _tokens(package.module.name)
    object_tokens |= set().union(*(_tokens(table.name) for table in package.tables), set())
    object_tokens |= set().union(*(_tokens(field.label) for field in package.fields), set())
    object_tokens |= set().union(*(_tokens(control.label) for control in package.controls), set())
    meaningful = tokens - set().union(*ACTION_WORDS.values())
    if not meaningful.intersection(object_tokens):
        _diagnostic(
            InferencePurposeGroundingError, location, "purpose_object_not_grounded", summary
        )
