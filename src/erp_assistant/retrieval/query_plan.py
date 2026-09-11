from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

_ACTION_INFINITIVES = (
    "eliminar|borrar|anular|modificar|editar|guardar|crear|registrar|"
    "aprobar|confirmar|restablecer|pagar"
)
_ACTION_IMPERATIVES = (
    "elimina|borra|anula|modifica|edita|guarda|crea|registra|"
    "aprueba|confirma|restablece|paga"
)
_ACTION_SUBJUNCTIVES = (
    "elimines|borres|anules|modifiques|edites|guardes|crees|registres|"
    "apruebes|confirmes|restablezcas|pagues"
)
_ACTION_OTHER_FORMS = (
    "elimino|eliminas|borro|borras|anulo|anulas|modifico|modificas|"
    "edito|editas|guardo|guardas|creo|creas|registro|registras|"
    "apruebo|apruebas|confirmo|confirmas|restablezco|restableces|"
    "pago|pagas"
)
_ACTION_WORD_PATTERN = (
    rf"\b(?:{_ACTION_INFINITIVES}|{_ACTION_IMPERATIVES}|"
    rf"{_ACTION_SUBJUNCTIVES}|{_ACTION_OTHER_FORMS})\b"
)

class QueryIntent(StrEnum):
    MUTATIVE_ACTION = "MUTATIVE_ACTION"
    SCREEN_PURPOSE = "SCREEN_PURPOSE"
    SEARCH_BY_FIELD = "SEARCH_BY_FIELD"
    LIST_FIELDS = "LIST_FIELDS"
    LOCATE_FIELD = "LOCATE_FIELD"
    LOCATE_SCREEN = "LOCATE_SCREEN"
    FIND_CONTROL = "FIND_CONTROL"
    LIST_COLUMNS = "LIST_COLUMNS"
    NAVIGATION_EVENT = "NAVIGATION_EVENT"


INTENT_ENTITY_TYPES: dict[QueryIntent, tuple[str, ...]] = {
    QueryIntent.MUTATIVE_ACTION: ("screen", "control", "event"),
    QueryIntent.SCREEN_PURPOSE: ("screen",),
    QueryIntent.SEARCH_BY_FIELD: ("screen", "field", "control"),
    QueryIntent.LIST_FIELDS: ("screen", "field", "control"),
    QueryIntent.LOCATE_FIELD: ("field", "screen", "module"),
    QueryIntent.LOCATE_SCREEN: ("screen", "module", "erp_system"),
    QueryIntent.FIND_CONTROL: ("control", "screen"),
    QueryIntent.LIST_COLUMNS: ("screen", "table", "table_column"),
    QueryIntent.NAVIGATION_EVENT: ("screen", "control", "ui_state", "event", "transition"),
}


@dataclass(frozen=True)
class QueryPlan:
    question: str
    normalized_question: str
    intent: QueryIntent | None
    target_entity_types: tuple[str, ...]
    requires_entity_resolution: bool
    requires_graph_context: bool
    requires_semantic_evidence: bool
    mutative_action: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "normalized_question": self.normalized_question,
            "intent": str(self.intent) if self.intent is not None else None,
            "target_entity_types": list(self.target_entity_types),
            "requires_entity_resolution": self.requires_entity_resolution,
            "requires_graph_context": self.requires_graph_context,
            "requires_semantic_evidence": self.requires_semantic_evidence,
            "mutative_action": self.mutative_action,
        }


class QueryPlanner:
    """Deterministic first-pass interpretation for hybrid retrieval.

    This planner deliberately does not resolve canonical entities and does not
    authorize anything. It only converts the user's wording into a stable query
    contract that downstream M3 components can consume and refine.
    """

    def plan(self, question: str) -> QueryPlan:
        clean_question = " ".join(str(question or "").split())
        normalized = self.normalize(clean_question)
        intent = self.detect_intent(clean_question, normalized=normalized)
        target_types = INTENT_ENTITY_TYPES.get(intent, ()) if intent is not None else ()
        execution_request = self.detect_execution_request(normalized)

        return QueryPlan(
            question=clean_question,
            normalized_question=normalized,
            intent=intent,
            target_entity_types=target_types,
            requires_entity_resolution=True,
            requires_graph_context=intent != QueryIntent.SCREEN_PURPOSE,
            requires_semantic_evidence=intent == QueryIntent.SCREEN_PURPOSE,
            mutative_action=execution_request,
        )

    @staticmethod
    def normalize(value: str) -> str:
        text = unicodedata.normalize("NFKD", str(value).casefold())
        text = "".join(char for char in text if not unicodedata.combining(char))
        return " ".join(re.sub(r"[^\w\s]", " ", text).split())

    @staticmethod
    def detect_execution_request(normalized: str) -> bool:
        """Return whether the user asks the assistant to execute a state-changing action.

        Action-oriented questions remain retrievable through MUTATIVE_ACTION intent,
        but only delegated/direct execution requests trigger the safety abstention flag.
        """

        text = QueryPlanner.normalize(normalized)
        if not re.search(_ACTION_WORD_PATTERN, text):
            return False

        if re.search(
            rf"^(?:por favor )?(?:{_ACTION_IMPERATIVES})\b",
            text,
        ):
            return True

        if re.search(
            rf"\b(?:puedes|podrias|puede|podria) "
            rf"(?:por favor )?(?:me )?(?:{_ACTION_INFINITIVES})\b",
            text,
        ):
            return True

        if re.search(
            rf"\b(?:quiero|necesito) que (?:me )?(?:{_ACTION_SUBJUNCTIVES})\b",
            text,
        ):
            return True

        return bool(
            re.search(r"\b(?:por mi|hazlo|ejecutalo|realizalo)\b", text)
        )

    @staticmethod
    def detect_intent(question: str, *, normalized: str | None = None) -> QueryIntent | None:
        q = str(question).casefold()
        normalized = normalized if normalized is not None else QueryPlanner.normalize(question)

        locative_action = bool(
            re.search(
                r"\b(?:donde|en (?:que|cual) (?:pantalla|modulo))\b",
                normalized,
            )
            and re.search(_ACTION_WORD_PATTERN, normalized)
            and not re.search(r"\b(?:campo|boton|control)\b", normalized)
        )
        if locative_action:
            return QueryIntent.LOCATE_SCREEN
        if re.search(_ACTION_WORD_PATTERN, normalized):
            return QueryIntent.MUTATIVE_ACTION
        if any(
            phrase in normalized
            for phrase in (
                "para que sirve",
                "que hace la pantalla",
                "que hace esta pantalla",
                "proposito de la pantalla",
                "cual es el proposito",
                "funcion de la pantalla",
                "para que se usa la pantalla",
                "que puedo hacer ahi",
                "que puedo hacer aqui",
                "que puedo hacer en esta pantalla",
                "que puedo hacer en esa pantalla",
                "que se puede hacer ahi",
                "que se puede hacer en esta pantalla",
                "que se puede hacer en esa pantalla",
            )
        ):
            return QueryIntent.SCREEN_PURPOSE
        # Explicit control language is more specific than the generic
        # search-by-field verb.  For example, "botón Buscar" asks where the
        # control is; it is not a request to search by a field named Buscar.
        if re.search(r"\b(botón|boton|control)\b", q):
            return QueryIntent.FIND_CONTROL
        if re.search(r"\b(buscar|busco|busca|búsqueda|busqueda|filtrar)\b", q):
            return QueryIntent.SEARCH_BY_FIELD
        if re.search(r"\b(campo|campos|filtro|filtros)\b", q):
            return QueryIntent.LIST_FIELDS
        if re.search(r"\b(dónde|donde|ingreso|aparece)\b.*\b(campo|ruc|identificaci)", q):
            return QueryIntent.LOCATE_FIELD
        if re.search(r"\b(módulo|modulo)\b", q) and (
            re.search(r"\b(?:en|a)\s+(?:qué|que|cuál|cual)\s+(?:módulo|modulo)\b", q)
            or re.search(r"\b(?:qué|que|cuál|cual)\s+(?:módulo|modulo)\b", q)
            or re.search(r"\b(?:módulo|modulo)\b.*\b(?:está|esta|pertenece)\b", q)
            or re.search(r"\b(?:está|esta|pertenece)\b.*\b(?:módulo|modulo)\b", q)
        ):
            return QueryIntent.LOCATE_SCREEN
        if re.search(r"\b(columnas|columna|tabla)\b", q):
            return QueryIntent.LIST_COLUMNS
        if re.search(r"\b(página|pagina|avanz|siguiente)\b", q):
            return QueryIntent.NAVIGATION_EVENT
        if re.search(
            r"\b(dónde|donde|ubico|encuentro|entro|accedo)\b",
            q,
        ):
            return QueryIntent.LOCATE_SCREEN
        return None
