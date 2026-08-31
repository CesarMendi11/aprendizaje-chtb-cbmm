from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from .entity_resolver import EntityResolution
from .query_plan import QueryIntent, QueryPlan, QueryPlanner


class ConversationContextMode(StrEnum):
    DIRECT = "DIRECT"
    CONTEXTUALIZED = "CONTEXTUALIZED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NO_CONTEXT = "NO_CONTEXT"


@dataclass(frozen=True)
class ConversationEntity:
    canonical_id: str
    entity_type: str
    safe_label: str
    route: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "entity_type": self.entity_type,
            "safe_label": self.safe_label,
            "route": self.route,
        }

    @classmethod
    def from_mapping(cls, row: Mapping[str, object] | None) -> ConversationEntity | None:
        if not row:
            return None
        canonical_id = str(row.get("canonical_id") or "").strip()
        entity_type = str(row.get("entity_type") or "").strip()
        safe_label = str(row.get("safe_label") or "").strip()
        if not canonical_id or not entity_type or not safe_label:
            return None
        route = str(row.get("route") or row.get("screen_route") or "").strip() or None
        return cls(
            canonical_id=canonical_id,
            entity_type=entity_type,
            safe_label=safe_label,
            route=route,
        )


@dataclass(frozen=True)
class ConversationState:
    erp_id: str | None = None
    knowledge_version: str | None = None
    current_screen: ConversationEntity | None = None
    current_module: ConversationEntity | None = None
    resolved_entities: tuple[ConversationEntity, ...] = ()
    unresolved_entities: tuple[ConversationEntity, ...] = ()
    last_intent: str | None = None
    last_answer_decision: str | None = None
    relevant_evidence_refs: tuple[str, ...] = ()
    turn_index: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "erp_id": self.erp_id,
            "knowledge_version": self.knowledge_version,
            "current_screen": self.current_screen.as_dict() if self.current_screen else None,
            "current_module": self.current_module.as_dict() if self.current_module else None,
            "resolved_entities": [row.as_dict() for row in self.resolved_entities],
            "unresolved_entities": [row.as_dict() for row in self.unresolved_entities],
            "last_intent": self.last_intent,
            "last_answer_decision": self.last_answer_decision,
            "relevant_evidence_refs": list(self.relevant_evidence_refs),
            "turn_index": self.turn_index,
        }

    @classmethod
    def coerce(cls, value: ConversationState | Mapping[str, object] | None) -> ConversationState:
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("conversation_state must be a ConversationState or mapping")

        def entity_list(raw: object) -> tuple[ConversationEntity, ...]:
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                return ()
            rows = []
            for item in raw:
                if isinstance(item, Mapping):
                    entity = ConversationEntity.from_mapping(item)
                    if entity is not None:
                        rows.append(entity)
            return tuple(rows)

        current_screen = ConversationEntity.from_mapping(
            value.get("current_screen") if isinstance(value.get("current_screen"), Mapping) else None
        )
        current_module = ConversationEntity.from_mapping(
            value.get("current_module") if isinstance(value.get("current_module"), Mapping) else None
        )
        evidence = value.get("relevant_evidence_refs")
        evidence_refs = (
            tuple(str(item) for item in evidence if str(item).strip())
            if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes))
            else ()
        )
        return cls(
            erp_id=str(value.get("erp_id") or "").strip() or None,
            knowledge_version=str(value.get("knowledge_version") or "").strip() or None,
            current_screen=current_screen,
            current_module=current_module,
            resolved_entities=entity_list(value.get("resolved_entities")),
            unresolved_entities=entity_list(value.get("unresolved_entities")),
            last_intent=str(value.get("last_intent") or "").strip() or None,
            last_answer_decision=str(value.get("last_answer_decision") or "").strip() or None,
            relevant_evidence_refs=evidence_refs,
            turn_index=max(0, int(value.get("turn_index") or 0)),
        )


@dataclass(frozen=True)
class ConversationContextResolution:
    mode: ConversationContextMode
    reason: str
    original_question: str
    effective_question: str
    inherited_entities: tuple[ConversationEntity, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": str(self.mode),
            "reason": self.reason,
            "original_question": self.original_question,
            "effective_question": self.effective_question,
            "inherited_entities": [row.as_dict() for row in self.inherited_entities],
        }


CONTEXT_REUSABLE_INTENTS = {
    QueryIntent.SCREEN_PURPOSE,
    QueryIntent.SEARCH_BY_FIELD,
    QueryIntent.LIST_FIELDS,
    QueryIntent.LOCATE_SCREEN,
    QueryIntent.FIND_CONTROL,
    QueryIntent.LIST_COLUMNS,
    QueryIntent.NAVIGATION_EVENT,
}

CONTEXT_REFERENCE_PHRASES = (
    "esta pantalla",
    "esa pantalla",
    "esta misma pantalla",
    "esa misma pantalla",
    "ahi",
    "alli",
    "aqui",
    "la anterior",
    "el anterior",
    "esa",
    "ese",
)


class ConversationContextResolver:
    """Resolve safe turn-to-turn references without sending transcript history to an LLM.

    The resolver can only inherit already-governed canonical entities from a
    ConversationState scoped to the same ERP and knowledge version. Explicit or
    sufficiently strong entity resolution in the current utterance always wins.
    """

    def __init__(self, *, query_planner: QueryPlanner | None = None):
        self.query_planner = query_planner or QueryPlanner()

    def resolve(
        self,
        question: str,
        state: ConversationState | Mapping[str, object] | None,
        *,
        query_plan: QueryPlan,
        direct_resolution: EntityResolution,
        erp_id: str,
        knowledge_version: str,
    ) -> ConversationContextResolution:
        state = ConversationState.coerce(state)
        normalized = self.query_planner.normalize(question)
        explicit_reference = self._references_prior_context(normalized)
        scoped_state = self._state_matches(state, erp_id, knowledge_version)

        # Current-turn evidence normally wins.  A contextual cue such as
        # "aquí" or "esta pantalla" is different: child labels like RUC,
        # Buscar, Nuevo or Siguiente página can be globally ambiguous while
        # the governed current screen already provides the missing scope.  In
        # that case we contextualize first and let PostgreSQL narrow the child
        # candidates to the inherited screen.  An explicit switch to another
        # screen/module/ERP still wins immediately.
        has_current_entity = self._has_current_turn_entity(direct_resolution)
        reuse_scope = bool(
            has_current_entity
            and explicit_reference
            and scoped_state
            and state.current_screen is not None
            and not self._has_explicit_scope_switch(direct_resolution, state)
        )
        if has_current_entity and not reuse_scope:
            return ConversationContextResolution(
                mode=ConversationContextMode.DIRECT,
                reason=(
                    "current_turn_ambiguity"
                    if direct_resolution.status == "ambiguous"
                    else "current_turn_entity"
                ),
                original_question=question,
                effective_question=question,
            )

        elliptical_reference = (
            scoped_state
            and query_plan.intent in CONTEXT_REUSABLE_INTENTS
            and len(normalized.split()) <= 8
        )
        wants_context = explicit_reference or elliptical_reference
        if not wants_context:
            return ConversationContextResolution(
                mode=ConversationContextMode.DIRECT,
                reason="no_context_reference",
                original_question=question,
                effective_question=question,
            )

        if not self._state_matches(state, erp_id, knowledge_version):
            reason = (
                "conversation_state_stale_or_foreign"
                if state.erp_id or state.knowledge_version
                else "conversation_reference_missing"
            )
            return ConversationContextResolution(
                mode=ConversationContextMode.CLARIFICATION_REQUIRED,
                reason=reason,
                original_question=question,
                effective_question=question,
            )

        inherited = self._reference_entities(state, query_plan)
        if not inherited:
            return ConversationContextResolution(
                mode=ConversationContextMode.CLARIFICATION_REQUIRED,
                reason="conversation_reference_missing",
                original_question=question,
                effective_question=question,
            )

        effective = self._contextualized_question(question, inherited)
        return ConversationContextResolution(
            mode=ConversationContextMode.CONTEXTUALIZED,
            reason="governed_entity_reference",
            original_question=question,
            effective_question=effective,
            inherited_entities=inherited,
        )

    def next_state(
        self,
        previous: ConversationState | Mapping[str, object] | None,
        *,
        erp_id: str,
        knowledge_version: str,
        query_plan: QueryPlan,
        answer_decision: str,
        sources: Sequence[Mapping[str, object]],
        clarification_candidates: Sequence[Mapping[str, object]],
        evidence_ids: Sequence[str],
    ) -> ConversationState:
        previous = ConversationState.coerce(previous)
        selected_entities = tuple(
            entity
            for entity in (ConversationEntity.from_mapping(row) for row in sources)
            if entity is not None
        )
        unresolved = tuple(
            entity
            for entity in (
                ConversationEntity.from_mapping(row) for row in clarification_candidates
            )
            if entity is not None
        )

        screen = next(
            (row for row in selected_entities if row.entity_type == "screen"),
            None,
        )
        module = next(
            (row for row in selected_entities if row.entity_type == "module"),
            None,
        )

        same_scope = self._state_matches(previous, erp_id, knowledge_version)
        selected_screen = screen
        if screen is None and same_scope:
            screen = previous.current_screen

        # A module may be inherited only while the conversation remains on the
        # same screen.  When the current turn explicitly resolves a different
        # screen and the selected evidence contains no module (for example an
        # ERP-root screen such as Dashboard), carrying the previous module
        # would create a false current_screen/current_module pairing.
        screen_changed = bool(
            same_scope
            and selected_screen is not None
            and (
                previous.current_screen is None
                or selected_screen.canonical_id
                != previous.current_screen.canonical_id
            )
        )
        if module is None and same_scope and not screen_changed:
            module = previous.current_module

        resolved = self._merge_entities(
            (screen, module),
            selected_entities,
            previous.resolved_entities if same_scope else (),
            limit=12,
        )
        evidence_refs = tuple(dict.fromkeys(str(item) for item in evidence_ids if item))[:20]

        return ConversationState(
            erp_id=erp_id,
            knowledge_version=knowledge_version,
            current_screen=screen,
            current_module=module,
            resolved_entities=resolved,
            unresolved_entities=unresolved,
            last_intent=str(query_plan.intent) if query_plan.intent is not None else None,
            last_answer_decision=answer_decision,
            relevant_evidence_refs=evidence_refs,
            turn_index=(previous.turn_index + 1) if same_scope else 1,
        )

    @staticmethod
    def _has_explicit_scope_switch(
        resolution: EntityResolution,
        state: ConversationState,
    ) -> bool:
        """Return True only for a strong top-level entity that changes scope."""

        current_screen = state.current_screen.canonical_id if state.current_screen else None
        current_module = state.current_module.canonical_id if state.current_module else None
        for candidate in resolution.candidates:
            if candidate.score < 0.80:
                continue
            if candidate.entity_type == "screen":
                if candidate.canonical_id != current_screen:
                    return True
                continue
            if candidate.entity_type == "module":
                if candidate.canonical_id != current_module:
                    return True
                continue
            if candidate.entity_type == "erp_system":
                return True
        return False

    @staticmethod
    def _has_current_turn_entity(resolution: EntityResolution) -> bool:
        if resolution.status == "ambiguous" or resolution.seed_candidates:
            return True
        return any(candidate.score >= 0.80 for candidate in resolution.candidates)

    @staticmethod
    def _state_matches(state: ConversationState, erp_id: str, knowledge_version: str) -> bool:
        return bool(
            state.erp_id == erp_id
            and state.knowledge_version == knowledge_version
        )

    @staticmethod
    def _references_prior_context(normalized: str) -> bool:
        if any(phrase in normalized for phrase in CONTEXT_REFERENCE_PHRASES):
            return True
        words = normalized.split()
        return bool(words and words[0] in {"y", "entonces", "ademas"})

    @staticmethod
    def _reference_entities(
        state: ConversationState,
        query_plan: QueryPlan,
    ) -> tuple[ConversationEntity, ...]:
        if state.current_screen is not None:
            return (state.current_screen,)
        if query_plan.intent == QueryIntent.LOCATE_SCREEN and state.current_module is not None:
            return (state.current_module,)
        return ()

    @staticmethod
    def _contextualized_question(
        question: str,
        inherited: Sequence[ConversationEntity],
    ) -> str:
        entity = inherited[0]
        type_name = {
            "screen": "pantalla",
            "module": "módulo",
        }.get(entity.entity_type, entity.entity_type)
        return f'{question.strip()} Referencia contextual validada: {type_name} "{entity.safe_label}".'

    @staticmethod
    def _merge_entities(*groups: Sequence[ConversationEntity | None], limit: int) -> tuple[ConversationEntity, ...]:
        merged: list[ConversationEntity] = []
        seen: set[str] = set()
        for group in groups:
            for entity in group:
                if entity is None or entity.canonical_id in seen:
                    continue
                seen.add(entity.canonical_id)
                merged.append(entity)
                if len(merged) >= limit:
                    return tuple(merged)
        return tuple(merged)


def render_missing_context_clarification(reason: str) -> str:
    if reason == "conversation_state_stale_or_foreign":
        return (
            "La referencia anterior ya no pertenece al conocimiento activo. "
            "Indícame nuevamente la pantalla o el módulo que quieres consultar."
        )
    return (
        "Necesito una pantalla o un módulo de referencia para continuar. "
        "Indícame cuál quieres consultar."
    )
