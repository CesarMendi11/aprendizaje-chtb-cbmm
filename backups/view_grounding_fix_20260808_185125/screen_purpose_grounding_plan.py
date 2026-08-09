from __future__ import annotations

from collections.abc import Iterable

from src.analysis.schemas.screen_evidence import ScreenEvidencePackage
from src.analysis.schemas.screen_purpose_grounding_plan import (
    ActionGroundingHint,
    RecognizedAction,
    ScreenPurposeGroundingPlan,
)
from src.knowledge.canonical.ids import normalize_text

ACTION_ORDER: tuple[RecognizedAction, ...] = (
    "search",
    "navigate",
    "view",
    "create",
    "edit",
    "delete",
    "process",
)
ACTION_TERMS: dict[RecognizedAction, frozenset[str]] = {
    "search": frozenset({"buscar", "busqueda", "consultar", "consulta", "filtrar", "filtro"}),
    "navigate": frozenset(
        {
            "navegar",
            "navegacion",
            "pagina",
            "paginacion",
            "pagination",
            "siguiente",
            "anterior",
            "primera",
            "ultima",
        }
    ),
    "view": frozenset({"visualizar", "mostrar", "listar", "ver"}),
    "create": frozenset(
        {"nuevo", "nueva", "crear", "creacion", "registrar", "registro", "guardar"}
    ),
    "edit": frozenset({"editar", "edicion", "modificar", "modificacion"}),
    "delete": frozenset({"eliminar", "eliminacion", "borrar"}),
    "process": frozenset({"procesar", "procesamiento"}),
}


def _tokens(*values: str | None) -> set[str]:
    return {
        token
        for value in values
        for token in normalize_text(value or "").split()
        if token
    }


def _matches(action: RecognizedAction, *values: str | None) -> bool:
    return bool(_tokens(*values) & ACTION_TERMS[action])


def _reference_type(reference_id: str) -> str:
    return reference_id.partition(":")[0]


def _hint(
    action: RecognizedAction,
    support_level: str,
    references: Iterable[str],
) -> ActionGroundingHint:
    evidence_refs = tuple(sorted(set(references)))
    return ActionGroundingHint(
        action=action,
        support_level=support_level,
        evidence_refs=evidence_refs,
        reference_types=tuple(sorted({_reference_type(item) for item in evidence_refs})),
        narrative_rule="direct_allowed" if support_level == "direct" else "prudent_only",
    )


def build_grounding_plan(package: ScreenEvidencePackage) -> ScreenPurposeGroundingPlan:
    """Derive the only functional actions demonstrated by a validated package."""
    hints: dict[RecognizedAction, ActionGroundingHint] = {}

    search_refs: set[str] = set()
    navigate_refs: set[str] = set()
    for control in package.controls:
        if _matches("search", control.label, control.control_type):
            search_refs.add(control.control_id)
        if _matches("navigate", control.label, control.control_type):
            navigate_refs.add(control.control_id)
    for event in package.events:
        if _matches("search", event.label, event.category):
            search_refs.add(event.event_id)
        if _matches("navigate", event.label, event.category):
            navigate_refs.add(event.event_id)

    if search_refs:
        search_refs.update(field.field_id for field in package.fields)
        hints["search"] = _hint("search", "direct", search_refs)
    if navigate_refs:
        trigger_ids = {
            control.control_id
            for control in package.controls
            if control.control_id in navigate_refs
        }
        navigate_refs.update(
            transition.transition_id
            for transition in package.transitions
            if _matches("navigate", transition.category)
            or transition.trigger_control_id in trigger_ids
        )
        hints["navigate"] = _hint("navigate", "direct", navigate_refs)

    view_refs = {package.screen_id}
    for table in package.tables:
        view_refs.add(table.table_id)
        view_refs.update(column.column_id for column in table.columns)
    if search_refs:
        view_refs.update(search_refs)
    hints["view"] = _hint("view", "direct", view_refs)

    for action in ("create", "edit", "delete", "process"):
        matching: list[tuple[str, str]] = []
        for control in package.controls:
            if _matches(action, control.label, control.control_type) and control.mutative:
                matching.append((control.control_id, control.safety_decision or "unknown"))
        for event in package.events:
            if _matches(action, event.label, event.category) and event.mutative:
                matching.append((event.event_id, event.policy_decision or "unknown"))
        allowed = {ref for ref, decision in matching if decision.casefold() == "allow"}
        prudent = {
            ref
            for ref, decision in matching
            if decision.casefold() not in {"allow", "deny"}
        }
        if allowed:
            hints[action] = _hint(action, "direct", allowed | prudent)
        elif prudent:
            hints[action] = _hint(action, "prudent_only", prudent)

    supported = tuple(hints[action] for action in ACTION_ORDER if action in hints)
    forbidden = tuple(action for action in ACTION_ORDER if action not in hints)
    return ScreenPurposeGroundingPlan(
        supported_actions=supported,
        forbidden_actions=forbidden,
    )
