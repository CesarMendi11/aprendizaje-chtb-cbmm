from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .entity_resolver import EntityResolution, normalize_entity_text
from .graph_expansion import GraphExpansionPlan
from .query_plan import QueryIntent, QueryPlan


@dataclass(frozen=True)
class EvidenceSelection:
    status: str
    reason: str
    focal_canonical_ids: tuple[str, ...]
    sources: tuple[Mapping[str, object], ...]
    relations: tuple[Mapping[str, object], ...]
    approved_semantics: tuple[Mapping[str, object], ...]
    clarification_candidates: tuple[Mapping[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "focal_canonical_ids": list(self.focal_canonical_ids),
            "source_ids": [
                row.get("canonical_id")
                for row in self.sources
                if row.get("canonical_id")
            ],
            "relation_types": [
                row.get("relationship_type")
                for row in self.relations
                if row.get("relationship_type")
            ],
            "semantic_ids": [
                row.get("semantic_id")
                for row in self.approved_semantics
                if row.get("semantic_id")
            ],
            "clarification_candidates": [dict(row) for row in self.clarification_candidates],
            "counts": {
                "sources": len(self.sources),
                "relations": len(self.relations),
                "approved_semantics": len(self.approved_semantics),
                "clarification_candidates": len(self.clarification_candidates),
            },
        }


class EvidenceSelector:
    """Reduce validated retrieval output to intent-specific answer evidence.

    Retrieval may intentionally be broad. This selector is the boundary that
    decides what the answer layer is allowed to see. It never creates new
    facts, does not reinterpret the question, and preserves canonical
    ambiguity instead of asking the LLM to choose among candidates.
    """

    def __init__(self, *, generic_source_limit: int = 6, generic_relation_limit: int = 10):
        self.generic_source_limit = max(1, int(generic_source_limit))
        self.generic_relation_limit = max(1, int(generic_relation_limit))

    def select(
        self,
        query_plan: QueryPlan,
        resolution: EntityResolution,
        graph_plan: GraphExpansionPlan,
        sources: Sequence[Mapping[str, object]],
        relations: Sequence[Mapping[str, object]],
        approved_semantics: Sequence[Mapping[str, object]],
    ) -> EvidenceSelection:
        if resolution.status == "ambiguous":
            return EvidenceSelection(
                status="clarification_required",
                reason="entity_resolution_ambiguous",
                focal_canonical_ids=(),
                sources=(),
                relations=(),
                approved_semantics=(),
                clarification_candidates=tuple(
                    {
                        "canonical_id": candidate.canonical_id,
                        "entity_type": candidate.entity_type,
                        "safe_label": candidate.safe_label,
                        "route": candidate.route,
                    }
                    for candidate in resolution.candidates
                    if candidate.canonical_id in set(resolution.ambiguous_candidate_ids)
                ),
            )

        source_by_id = {
            str(row.get("canonical_id")): row
            for row in sources
            if row.get("canonical_id")
        }
        focal_ids = self._focal_ids(
            query_plan,
            resolution,
            graph_plan,
            sources,
            approved_semantics,
        )

        if query_plan.intent == QueryIntent.SCREEN_PURPOSE:
            semantics = self._purpose_semantics(focal_ids, approved_semantics)
            selected_ids = {
                str(row.get("screen_id"))
                for row in semantics
                if row.get("screen_id")
            }
            selected_sources = self._ordered_sources(
                sources,
                selected_ids or set(focal_ids),
            )
            if not semantics:
                return EvidenceSelection(
                    status="insufficient",
                    reason="screen_purpose_semantic_missing",
                    focal_canonical_ids=tuple(i for i in focal_ids if i),
                    sources=selected_sources,
                    relations=(),
                    approved_semantics=(),
                )
            return self._selection(
                reason="screen_purpose",
                focal_ids=focal_ids,
                sources=selected_sources,
                relations=(),
                semantics=semantics,
            )

        if query_plan.intent == QueryIntent.LOCATE_SCREEN:
            screen_id = self._first_focal_of_type(focal_ids, source_by_id, "screen")
            selected_relations = self._locate_screen_relations(screen_id, relations)
            selected_ids = self._relation_ids(selected_relations)
            if screen_id:
                selected_ids.add(screen_id)
            return self._selection(
                reason="locate_screen",
                focal_ids=(screen_id,) if screen_id else focal_ids,
                sources=self._ordered_sources(sources, selected_ids),
                relations=selected_relations,
                semantics=(),
            )

        if query_plan.intent == QueryIntent.LOCATE_FIELD:
            selected_relations = self._locate_field_relations(focal_ids, relations)
            return self._from_relations(
                reason="locate_field",
                focal_ids=focal_ids,
                sources=sources,
                relations=selected_relations,
            )

        if query_plan.intent == QueryIntent.FIND_CONTROL:
            selected_relations = self._target_relations(
                focal_ids,
                relations,
                relationship_types={"HAS_CONTROL"},
            )
            return self._from_relations(
                reason="find_control",
                focal_ids=focal_ids,
                sources=sources,
                relations=selected_relations,
            )

        if query_plan.intent == QueryIntent.SEARCH_BY_FIELD:
            selected_relations = self._screen_member_relations(
                focal_ids,
                source_by_id,
                relations,
                relationship_types={"HAS_FIELD", "HAS_CONTROL"},
                control_filter=True,
            )
            return self._from_relations(
                reason="search_by_field",
                focal_ids=focal_ids,
                sources=sources,
                relations=selected_relations,
            )

        if query_plan.intent == QueryIntent.LIST_FIELDS:
            selected_relations = self._screen_member_relations(
                focal_ids,
                source_by_id,
                relations,
                relationship_types={"HAS_FIELD", "HAS_CONTROL"},
                control_limit=8,
            )
            return self._from_relations(
                reason="list_fields",
                focal_ids=focal_ids,
                sources=sources,
                relations=selected_relations,
            )

        if query_plan.intent == QueryIntent.LIST_COLUMNS:
            selected_relations = self._table_relations(focal_ids, source_by_id, relations)
            selected_ids = self._relation_ids(selected_relations)
            selected_focal = tuple(
                canonical_id
                for canonical_id in focal_ids
                if source_by_id.get(canonical_id, {}).get("entity_type")
                in {"screen", "table", "table_column"}
            )
            selected_ids.update(selected_focal)
            return self._selection(
                reason="list_columns",
                focal_ids=selected_focal or focal_ids,
                sources=self._ordered_sources(sources, selected_ids),
                relations=selected_relations,
                semantics=(),
            )

        if query_plan.intent == QueryIntent.NAVIGATION_EVENT:
            selected_relations = self._navigation_relations(
                query_plan,
                focal_ids,
                relations,
            )
            return self._from_relations(
                reason="navigation_event",
                focal_ids=focal_ids,
                sources=sources,
                relations=selected_relations,
            )

        if query_plan.intent == QueryIntent.MUTATIVE_ACTION:
            selected_relations = tuple(
                row
                for row in relations
                if row.get("relationship_type")
                in {
                    "HAS_CONTROL",
                    "HAS_EVENT",
                    "FROM_STATE",
                    "TO_STATE",
                    "TRIGGERED_BY",
                }
            )[:32]
            return self._from_relations(
                reason="mutative_evidence",
                focal_ids=focal_ids,
                sources=sources,
                relations=selected_relations,
            )

        # Unknown intents remain bounded. This is enough for grounded generation
        # without passing the whole retrieval neighborhood to the model.
        generic_sources = tuple(sources[: self.generic_source_limit])
        generic_ids = {
            str(row.get("canonical_id"))
            for row in generic_sources
            if row.get("canonical_id")
        }
        generic_relations = tuple(
            row
            for row in relations
            if {
                str(row.get("source_canonical_id") or ""),
                str(row.get("target_canonical_id") or ""),
            }
            & generic_ids
        )[: self.generic_relation_limit]
        return self._selection(
            reason="bounded_generic",
            focal_ids=focal_ids,
            sources=generic_sources,
            relations=generic_relations,
            semantics=tuple(approved_semantics[:2]),
        )

    @staticmethod
    def _selection(*, reason, focal_ids, sources, relations, semantics):
        status = "selected" if sources or relations or semantics else "insufficient"
        return EvidenceSelection(
            status=status,
            reason=reason if status == "selected" else f"{reason}_insufficient",
            focal_canonical_ids=tuple(i for i in focal_ids if i),
            sources=tuple(sources),
            relations=tuple(relations),
            approved_semantics=tuple(semantics),
        )

    def _from_relations(self, *, reason, focal_ids, sources, relations):
        selected_ids = self._relation_ids(relations)
        selected_ids.update(i for i in focal_ids if i)
        return self._selection(
            reason=reason,
            focal_ids=focal_ids,
            sources=self._ordered_sources(sources, selected_ids),
            relations=relations,
            semantics=(),
        )


    @staticmethod
    def _navigation_relations(query_plan, focal_ids, relations):
        """Prefer one explicit governed navigation affordance.

        Contextualization can make the current screen the only strong canonical
        resolution seed even when the requested paginator control is present in
        the graph neighborhood. In that case, select the direct HAS_EVENT or
        HAS_CONTROL whose safe label is actually mentioned by the user's
        question instead of forwarding the whole screen neighborhood.
        """

        focal = set(focal_ids)
        direct = tuple(
            row
            for row in relations
            if row.get("relationship_type") in {"HAS_EVENT", "HAS_CONTROL"}
            and row.get("target_canonical_id") in focal
        )
        if direct:
            return direct[:1]

        normalized_question = normalize_entity_text(query_plan.question)
        question_tokens = set(normalized_question.split())
        generic_navigation_tokens = {
            "pagina",
            "control",
            "boton",
            "evento",
            "navegacion",
        }

        def label_matches(row):
            label = normalize_entity_text(str(row.get("target_label") or ""))
            if not label:
                return False
            if label in normalized_question:
                return True
            tokens = {
                token
                for token in label.split()
                if token not in generic_navigation_tokens
            }
            return bool(tokens) and tokens.issubset(question_tokens)

        named = [
            row
            for row in relations
            if row.get("relationship_type") in {"HAS_EVENT", "HAS_CONTROL"}
            and label_matches(row)
        ]
        if named:
            named.sort(
                key=lambda row: (
                    0 if row.get("relationship_type") == "HAS_EVENT" else 1,
                    str(row.get("target_label") or ""),
                )
            )
            return (named[0],)

        return tuple(
            row
            for row in relations
            if row.get("relationship_type")
            in {
                "HAS_CONTROL",
                "HAS_STATE",
                "HAS_EVENT",
                "FROM_STATE",
                "TO_STATE",
                "TRIGGERED_BY",
            }
        )[:32]

    @staticmethod
    def _ordered_sources(sources, selected_ids):
        return tuple(
            row
            for row in sources
            if row.get("canonical_id") in selected_ids
        )

    @staticmethod
    def _relation_ids(relations: Iterable[Mapping[str, object]]) -> set[str]:
        ids: set[str] = set()
        for row in relations:
            for key in ("source_canonical_id", "target_canonical_id"):
                value = row.get(key)
                if value:
                    ids.add(str(value))
        return ids

    @staticmethod
    def _focal_ids(query_plan, resolution, graph_plan, sources, approved_semantics):
        ordered: list[str] = []

        def add(value):
            value = str(value or "").strip()
            if value and value not in ordered:
                ordered.append(value)

        add(resolution.primary_canonical_id)
        for value in graph_plan.seed_canonical_ids:
            add(value)
        for candidate in resolution.seed_candidates:
            add(candidate.canonical_id)
        if (
            query_plan.intent == QueryIntent.SCREEN_PURPOSE
            and not ordered
            and len(approved_semantics) == 1
        ):
            add(approved_semantics[0].get("screen_id"))
        if not ordered:
            for row in sources[:3]:
                add(row.get("canonical_id"))
        return tuple(ordered)

    @staticmethod
    def _first_focal_of_type(focal_ids, source_by_id, entity_type):
        for canonical_id in focal_ids:
            row = source_by_id.get(canonical_id)
            if row and row.get("entity_type") == entity_type:
                return canonical_id
        return None

    @staticmethod
    def _purpose_semantics(focal_ids, approved_semantics):
        focal = set(focal_ids)
        matches = tuple(
            row
            for row in approved_semantics
            if row.get("screen_id") in focal
        )
        return matches[:2]

    @staticmethod
    def _locate_screen_relations(screen_id, relations):
        if not screen_id:
            return ()
        matches = [
            row
            for row in relations
            if row.get("relationship_type") == "HAS_SCREEN"
            and row.get("target_canonical_id") == screen_id
        ]
        module = next((row for row in matches if row.get("source_type") == "module"), None)
        return (module,) if module is not None else tuple(matches[:1])

    @staticmethod
    def _locate_field_relations(focal_ids, relations):
        focal = set(focal_ids)
        fields = [
            row
            for row in relations
            if row.get("relationship_type") == "HAS_FIELD"
            and (
                row.get("target_canonical_id") in focal
                or not focal
            )
        ]
        if not fields:
            fields = [
                row
                for row in relations
                if row.get("relationship_type") == "HAS_FIELD"
            ][:1]
        selected = list(fields)
        screen_ids = {row.get("source_canonical_id") for row in fields}
        selected.extend(
            row
            for row in relations
            if row.get("relationship_type") == "HAS_SCREEN"
            and row.get("target_canonical_id") in screen_ids
        )
        return tuple(selected)

    @staticmethod
    def _target_relations(focal_ids, relations, *, relationship_types):
        focal = set(focal_ids)
        matches = [
            row
            for row in relations
            if row.get("relationship_type") in relationship_types
            and row.get("target_canonical_id") in focal
        ]
        if matches:
            return tuple(matches)
        return tuple(
            row
            for row in relations
            if row.get("relationship_type") in relationship_types
        )[:1]

    @staticmethod
    def _screen_ids(focal_ids, source_by_id, relations):
        screens = [
            canonical_id
            for canonical_id in focal_ids
            if source_by_id.get(canonical_id, {}).get("entity_type") == "screen"
        ]
        if screens:
            return tuple(screens)
        for row in relations:
            if row.get("relationship_type") in {
                "HAS_FIELD",
                "HAS_CONTROL",
                "HAS_TABLE",
                "HAS_STATE",
                "HAS_EVENT",
            }:
                source_id = row.get("source_canonical_id")
                if source_by_id.get(source_id, {}).get("entity_type") == "screen":
                    return (source_id,)
        return ()

    def _screen_member_relations(
        self,
        focal_ids,
        source_by_id,
        relations,
        *,
        relationship_types,
        control_filter=False,
        control_limit=None,
    ):
        screen_ids = set(self._screen_ids(focal_ids, source_by_id, relations))
        rows = [
            row
            for row in relations
            if row.get("relationship_type") in relationship_types
            and (not screen_ids or row.get("source_canonical_id") in screen_ids)
        ]
        if control_filter:
            fields = [row for row in rows if row.get("relationship_type") == "HAS_FIELD"]
            controls = [
                row
                for row in rows
                if row.get("relationship_type") == "HAS_CONTROL"
                and any(
                    token in str(row.get("target_label") or "").casefold()
                    for token in ("buscar", "search", "filtrar", "filtro")
                )
            ]
            if not controls:
                controls = [row for row in rows if row.get("relationship_type") == "HAS_CONTROL"][:1]
            rows = fields + controls
        elif control_limit is not None:
            fields = [row for row in rows if row.get("relationship_type") == "HAS_FIELD"]
            controls = [row for row in rows if row.get("relationship_type") == "HAS_CONTROL"]
            rows = fields + controls[: max(0, int(control_limit))]
        return tuple(rows)

    def _table_relations(self, focal_ids, source_by_id, relations):
        screen_ids = set(self._screen_ids(focal_ids, source_by_id, relations))
        tables = [
            row
            for row in relations
            if row.get("relationship_type") == "HAS_TABLE"
            and (not screen_ids or row.get("source_canonical_id") in screen_ids)
        ]
        table_ids = {row.get("target_canonical_id") for row in tables}
        columns = [
            row
            for row in relations
            if row.get("relationship_type") == "HAS_COLUMN"
            and row.get("source_canonical_id") in table_ids
        ]
        return tuple(tables + columns)
