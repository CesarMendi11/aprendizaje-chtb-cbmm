from __future__ import annotations

from .evidence_selector import EvidenceSelection
from .query_plan import QueryPlan

TYPE_NAMES = {
    "erp_system": "ERP",
    "module": "módulo",
    "screen": "pantalla",
    "ui_state": "estado",
    "field": "campo",
    "control": "control",
    "table": "tabla",
    "table_column": "columna",
    "event": "evento",
    "transition": "transición",
    "link": "enlace",
}


class EvidenceContextBuilder:
    """Build the bounded, validated text that may reach grounded generation."""

    def __init__(self, *, max_chars: int = 6000):
        self.max_chars = max(500, int(max_chars))

    def build(self, query_plan: QueryPlan, selection: EvidenceSelection) -> str:
        if selection.status != "selected":
            return ""

        sections: list[str] = []

        entities = []
        for row in selection.sources:
            label = str(row.get("safe_label") or "Entidad validada")
            entity_type = str(row.get("entity_type") or "entity")
            route = str(row.get("screen_route") or "").strip()
            suffix = f" | ruta: {route}" if route and entity_type == "screen" else ""
            entities.append(f"- {TYPE_NAMES.get(entity_type, entity_type)}: {label}{suffix}")
        if entities:
            sections.append("ENTIDADES SELECCIONADAS\n" + "\n".join(entities))

        semantic_facts = []
        for row in selection.approved_semantics:
            label = str(row.get("safe_label") or "Pantalla validada")
            summary = str(row.get("purpose_summary") or "").strip()
            if summary:
                semantic_facts.append(f'- Propósito aprobado de "{label}": {summary}')
            semantic_facts.extend(
                f'- Capacidad aprobada de "{label}": {statement}'
                for statement in row.get("supported_capabilities", [])
                if statement
            )
        if semantic_facts:
            sections.append("SEMÁNTICA HUMANA APROBADA\n" + "\n".join(semantic_facts))

        relation_facts = [self._natural_fact(row) for row in selection.relations]
        if relation_facts:
            sections.append("RELACIONES SELECCIONADAS\n" + "\n".join(relation_facts))

        if not sections:
            return ""

        header = (
            "CONTEXTO VALIDADO Y SELECCIONADO\n"
            f"Intent: {query_plan.intent or 'UNKNOWN'}\n"
            "Usa únicamente estos hechos.\n\n"
        )
        return (header + "\n\n".join(sections))[: self.max_chars]

    @staticmethod
    def _natural_fact(row) -> str:
        templates = {
            "HAS_MODULE": 'El ERP "{s}" contiene el módulo "{t}".',
            "HAS_SUBMODULE": 'El módulo "{s}" contiene el submódulo "{t}".',
            "HAS_SCREEN": 'El {st} "{s}" contiene la pantalla "{t}".',
            "HAS_FIELD": 'La pantalla "{s}" contiene el campo "{t}".',
            "HAS_CONTROL": 'La pantalla "{s}" contiene el control "{t}".',
            "HAS_TABLE": 'La pantalla "{s}" contiene la tabla "{t}".',
            "HAS_COLUMN": 'La tabla "{s}" contiene la columna "{t}".',
            "HAS_STATE": 'La pantalla "{s}" contiene el estado "{t}".',
            "HAS_LINK": 'La pantalla "{s}" contiene el enlace "{t}".',
            "TARGETS": 'El enlace "{s}" lleva a la pantalla "{t}".',
            "HAS_EVENT": 'En la pantalla "{s}" se observó el evento "{t}".',
            "FROM_STATE": 'El {st} "{s}" parte del estado "{t}".',
            "TO_STATE": 'El {st} "{s}" conduce al estado "{t}".',
            "TRIGGERED_BY": 'La transición "{s}" se activa mediante el evento "{t}".',
        }
        template = templates.get(row.get("relationship_type"))
        if template:
            return template.format(
                s=row.get("source_label") or "Entidad validada",
                t=row.get("target_label") or "Entidad validada",
                st=TYPE_NAMES.get(
                    row.get("source_type"),
                    row.get("source_type") or "entidad",
                ),
            )
        return (
            f'"{row.get("source_label") or "Entidad validada"}" '
            f"está relacionado con "
            f'"{row.get("target_label") or "Entidad validada"}".'
        )
