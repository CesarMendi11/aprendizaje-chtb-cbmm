from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .entity_resolver import EntityResolution
from .query_plan import QueryIntent, QueryPlan
from .rank_fusion import FusedCandidate


@dataclass(frozen=True)
class GraphTraversalPolicy:
    name: str
    seed_entity_types: tuple[str, ...]
    endpoint_entity_types: tuple[str, ...]
    relationships: tuple[str, ...]
    max_hops: int
    max_seeds: int = 3
    minimum_limit: int = 20


@dataclass(frozen=True)
class GraphExpansionPlan:
    enabled: bool
    strategy: str
    reason: str
    seed_canonical_ids: tuple[str, ...]
    seed_entity_types: tuple[str, ...]
    endpoint_entity_types: tuple[str, ...]
    relationships: tuple[str, ...]
    max_hops: int
    limit: int

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy,
            "reason": self.reason,
            "seed_canonical_ids": list(self.seed_canonical_ids),
            "seed_entity_types": list(self.seed_entity_types),
            "endpoint_entity_types": list(self.endpoint_entity_types),
            "relationships": list(self.relationships),
            "max_hops": self.max_hops,
            "limit": self.limit,
        }


POLICIES: dict[QueryIntent, GraphTraversalPolicy] = {
    QueryIntent.SCREEN_PURPOSE: GraphTraversalPolicy(
        name="screen_purpose",
        seed_entity_types=("screen",),
        endpoint_entity_types=("screen",),
        relationships=(),
        max_hops=1,
    ),
    QueryIntent.LOCATE_SCREEN: GraphTraversalPolicy(
        name="locate_screen",
        seed_entity_types=("screen", "module", "erp_system", "ui_state"),
        endpoint_entity_types=("screen", "module", "erp_system", "ui_state"),
        relationships=("HAS_MODULE", "HAS_SUBMODULE", "HAS_SCREEN", "HAS_STATE"),
        max_hops=2,
    ),
    QueryIntent.LOCATE_FIELD: GraphTraversalPolicy(
        name="locate_field",
        seed_entity_types=("field", "screen", "module", "ui_state"),
        endpoint_entity_types=("field", "screen", "module", "ui_state"),
        relationships=("HAS_FIELD", "HAS_SCREEN", "HAS_STATE"),
        max_hops=2,
    ),
    QueryIntent.FIND_CONTROL: GraphTraversalPolicy(
        name="find_control",
        seed_entity_types=("control", "screen", "ui_state"),
        endpoint_entity_types=("control", "screen", "ui_state"),
        relationships=("HAS_CONTROL", "HAS_STATE"),
        max_hops=2,
    ),
    QueryIntent.SEARCH_BY_FIELD: GraphTraversalPolicy(
        name="search_by_field",
        seed_entity_types=("screen", "field", "control", "ui_state"),
        endpoint_entity_types=("screen", "field", "control", "ui_state"),
        relationships=("HAS_FIELD", "HAS_CONTROL", "HAS_STATE"),
        max_hops=2,
        minimum_limit=64,
    ),
    QueryIntent.LIST_FIELDS: GraphTraversalPolicy(
        name="list_fields",
        seed_entity_types=("screen", "field", "control", "ui_state"),
        endpoint_entity_types=("screen", "field", "control", "ui_state"),
        relationships=("HAS_FIELD", "HAS_CONTROL", "HAS_STATE"),
        max_hops=2,
        minimum_limit=64,
    ),
    QueryIntent.LIST_COLUMNS: GraphTraversalPolicy(
        name="list_columns",
        seed_entity_types=("screen", "table", "table_column", "ui_state"),
        endpoint_entity_types=("screen", "table", "table_column", "ui_state"),
        relationships=("HAS_STATE", "HAS_TABLE", "HAS_COLUMN"),
        # A structural dense hit can be a UIState. Three hops are then needed
        # to reach UIState <- Screen -> Table -> TableColumn without a generic
        # second expansion pass.
        max_hops=3,
        minimum_limit=64,
    ),
    QueryIntent.NAVIGATION_EVENT: GraphTraversalPolicy(
        name="navigation_event",
        # Prefer a concrete governed navigation target before the contextual
        # screen. Some ERP affordances are represented structurally as Events,
        # while others (for example a paginator button) exist only as Controls
        # when no transition was observed. Both remain screen-scoped evidence.
        seed_entity_types=("event", "control", "transition", "ui_state", "screen"),
        endpoint_entity_types=("screen", "control", "ui_state", "event", "transition"),
        relationships=(
            "HAS_CONTROL",
            "HAS_STATE",
            "HAS_EVENT",
            "FROM_STATE",
            "TO_STATE",
            "TRIGGERED_BY",
        ),
        max_hops=2,
        minimum_limit=64,
    ),
    QueryIntent.MUTATIVE_ACTION: GraphTraversalPolicy(
        name="mutative_evidence",
        seed_entity_types=("screen", "ui_state", "control", "event", "transition"),
        endpoint_entity_types=("screen", "ui_state", "control", "event", "transition"),
        relationships=(
            "HAS_CONTROL",
            "HAS_STATE",
            "HAS_EVENT",
            "FROM_STATE",
            "TO_STATE",
            "TRIGGERED_BY",
        ),
        max_hops=2,
        minimum_limit=64,
    ),
}

FALLBACK_POLICY = GraphTraversalPolicy(
    name="generic_fallback",
    seed_entity_types=(),
    endpoint_entity_types=(),
    relationships=(
        "HAS_MODULE",
        "HAS_SUBMODULE",
        "HAS_SCREEN",
        "HAS_STATE",
        "HAS_FIELD",
        "HAS_CONTROL",
        "HAS_TABLE",
        "HAS_COLUMN",
        "HAS_LINK",
        "HAS_EVENT",
        "FROM_STATE",
        "TO_STATE",
        "TRIGGERED_BY",
    ),
    max_hops=2,
)


class QueryAwareGraphExpansionPlanner:
    """Select a bounded Neo4j traversal from the already-governed QueryPlan.

    This component does not authorize entities and does not reinterpret the
    user's question. It narrows graph seeds, relationship types and hop depth
    after PostgreSQL validation of RRF candidates. Ambiguous canonical entity
    resolution is preserved instead of being silently resolved by graph noise.
    """

    def plan(
        self,
        query_plan: QueryPlan,
        resolution: EntityResolution,
        fused: Sequence[FusedCandidate],
        *,
        candidate_types: Mapping[str, str],
        graph_limit: int,
    ) -> GraphExpansionPlan:
        policy = POLICIES.get(query_plan.intent, FALLBACK_POLICY)
        limit = max(int(graph_limit), policy.minimum_limit)

        if not query_plan.requires_graph_context:
            return self._disabled(
                policy,
                reason="query_plan_no_graph_context",
                limit=limit,
            )

        if resolution.status == "ambiguous":
            return self._disabled(
                policy,
                reason="entity_resolution_ambiguous",
                limit=limit,
            )

        ambiguous_ids = set(resolution.ambiguous_candidate_ids)

        def allowed(canonical_id: str) -> bool:
            if not canonical_id or canonical_id in ambiguous_ids:
                return False
            entity_type = candidate_types.get(canonical_id)
            if entity_type is None:
                return False
            return not policy.seed_entity_types or entity_type in policy.seed_entity_types

        ordered: list[str] = []

        # A single strong canonical entity is the safest and most useful graph
        # anchor. It prevents dense-only neighbors from becoming unnecessary
        # parallel seeds for questions such as "¿Dónde configuro los años?".
        primary = resolution.primary_canonical_id
        if primary and allowed(primary):
            ordered.append(primary)
        else:
            strong = [
                candidate
                for candidate in resolution.seed_candidates
                if allowed(candidate.canonical_id)
            ]
            if strong and policy.seed_entity_types:
                type_priority = {
                    entity_type: index for index, entity_type in enumerate(policy.seed_entity_types)
                }
                best_priority = min(
                    type_priority.get(
                        candidate_types[candidate.canonical_id],
                        len(type_priority),
                    )
                    for candidate in strong
                )
                strong = [
                    candidate
                    for candidate in strong
                    if type_priority.get(
                        candidate_types[candidate.canonical_id],
                        len(type_priority),
                    )
                    == best_priority
                ]

            for candidate in strong:
                if candidate.canonical_id not in ordered:
                    ordered.append(candidate.canonical_id)
                    if len(ordered) >= policy.max_seeds:
                        break

            if not ordered:
                for candidate in fused:
                    if allowed(candidate.canonical_id) and candidate.canonical_id not in ordered:
                        ordered.append(candidate.canonical_id)
                        if len(ordered) >= policy.max_seeds:
                            break

        if not ordered:
            return self._disabled(
                policy,
                reason="no_valid_query_aware_seeds",
                limit=limit,
            )

        return GraphExpansionPlan(
            enabled=True,
            strategy=policy.name,
            reason="query_aware_policy",
            seed_canonical_ids=tuple(ordered),
            seed_entity_types=tuple(candidate_types[canonical_id] for canonical_id in ordered),
            endpoint_entity_types=policy.endpoint_entity_types,
            relationships=policy.relationships,
            max_hops=policy.max_hops,
            limit=limit,
        )

    @staticmethod
    def _disabled(
        policy: GraphTraversalPolicy,
        *,
        reason: str,
        limit: int,
    ) -> GraphExpansionPlan:
        return GraphExpansionPlan(
            enabled=False,
            strategy=policy.name,
            reason=reason,
            seed_canonical_ids=(),
            seed_entity_types=(),
            endpoint_entity_types=policy.endpoint_entity_types,
            relationships=policy.relationships,
            max_hops=policy.max_hops,
            limit=limit,
        )
