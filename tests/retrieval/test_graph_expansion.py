from erp_assistant.retrieval.entity_resolver import EntityResolution, EntityResolutionCandidate
from erp_assistant.retrieval.graph_expansion import QueryAwareGraphExpansionPlanner
from erp_assistant.retrieval.query_plan import QueryPlanner
from erp_assistant.retrieval.rank_fusion import FusedCandidate


def _fused(*canonical_ids):
    return tuple(
        FusedCandidate(canonical_id=canonical_id, rrf_score=0.01, contributions=())
        for canonical_id in canonical_ids
    )


def _candidate(canonical_id, entity_type, label, *, score=1.0):
    return EntityResolutionCandidate(
        canonical_id=canonical_id,
        entity_type=entity_type,
        safe_label=label,
        route=None,
        score=score,
        channels=("normalized_mention",),
        matched_terms=(label.casefold(),),
        channel_scores=(("normalized_mention", score),),
    )


def test_locate_screen_uses_single_strong_canonical_screen_seed():
    query_plan = QueryPlanner().plan("¿Dónde configuro los años?")
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(_candidate("screen:ano", "screen", "Año"),),
    )

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("screen:ano", "ui_state:ano", "control:noise"),
        candidate_types={
            "screen:ano": "screen",
            "ui_state:ano": "ui_state",
            "control:noise": "control",
        },
        graph_limit=20,
    )

    assert plan.enabled is True
    assert plan.strategy == "locate_screen"
    assert plan.seed_canonical_ids == ("screen:ano",)
    assert plan.seed_entity_types == ("screen",)
    assert set(plan.relationships) == {
        "HAS_MODULE",
        "HAS_SUBMODULE",
        "HAS_SCREEN",
        "HAS_STATE",
    }
    assert plan.max_hops == 2


def test_ambiguous_entity_resolution_blocks_graph_expansion():
    query_plan = QueryPlanner().plan("¿Dónde aparece la identificación tributaria?")
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(
            _candidate("field:ruc-1", "field", "RUC", score=0.99),
            _candidate("field:ruc-2", "field", "RUC", score=0.99),
        ),
    )

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("field:ruc-1", "field:ruc-2", "screen:noise"),
        candidate_types={
            "field:ruc-1": "field",
            "field:ruc-2": "field",
            "screen:noise": "screen",
        },
        graph_limit=20,
    )

    assert plan.enabled is False
    assert plan.reason == "entity_resolution_ambiguous"
    assert plan.seed_canonical_ids == ()


def test_screen_purpose_does_not_expand_graph():
    query_plan = QueryPlanner().plan("¿Para qué sirve Año?")
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(_candidate("screen:ano", "screen", "Año"),),
    )

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("screen:ano"),
        candidate_types={"screen:ano": "screen"},
        graph_limit=20,
    )

    assert plan.enabled is False
    assert plan.reason == "query_plan_no_graph_context"
    assert plan.strategy == "screen_purpose"


def test_typo_can_use_rrf_screen_as_query_aware_seed_without_primary_entity():
    query_plan = QueryPlanner().plan("¿Dónde está anoo?")
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(
            EntityResolutionCandidate(
                canonical_id="screen:ano",
                entity_type="screen",
                safe_label="Año",
                route="/admin/general/anios",
                score=0.835,
                channels=("trigram",),
                matched_terms=(),
                channel_scores=(("trigram", 0.835),),
            ),
        ),
    )

    assert resolution.primary_canonical_id is None

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("screen:ano", "control:noise"),
        candidate_types={
            "screen:ano": "screen",
            "control:noise": "control",
        },
        graph_limit=20,
    )

    assert plan.enabled is True
    assert plan.seed_canonical_ids == ("screen:ano",)


def test_list_columns_policy_can_bridge_ui_state_to_table_column_in_three_hops():
    query_plan = QueryPlanner().plan(
        "¿Qué información aparece en la tabla de Comprobantes electrónicos emitidos?"
    )
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(),
    )

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("ui_state:comp", "control:noise"),
        candidate_types={
            "ui_state:comp": "ui_state",
            "control:noise": "control",
        },
        graph_limit=20,
    )

    assert plan.enabled is True
    assert plan.strategy == "list_columns"
    assert plan.seed_canonical_ids == ("ui_state:comp",)
    assert plan.max_hops == 3
    assert plan.limit == 64
    assert set(plan.relationships) == {"HAS_STATE", "HAS_TABLE", "HAS_COLUMN"}
    assert set(plan.endpoint_entity_types) == {
        "screen",
        "ui_state",
        "table",
        "table_column",
    }


def test_list_columns_prefers_strong_screen_over_same_label_column_seed():
    query_plan = QueryPlanner().plan("¿Qué columnas tiene Año?")
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(
            _candidate("screen:ano", "screen", "Año"),
            _candidate("table_column:ano", "table_column", "AÑO"),
        ),
    )

    assert resolution.primary_canonical_id is None

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("screen:ano", "table_column:ano"),
        candidate_types={
            "screen:ano": "screen",
            "table_column:ano": "table_column",
        },
        graph_limit=20,
    )

    assert plan.enabled is True
    assert plan.strategy == "list_columns"
    assert plan.seed_canonical_ids == ("screen:ano",)


def test_navigation_event_prefers_explicit_event_over_contextual_screen_seed():
    query_plan = QueryPlanner().plan(
        '¿Cómo avanzo a la siguiente página aquí? Referencia contextual validada: '
        'pantalla "Comprobantes eléctronicos emitidos".'
    )
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(
            _candidate(
                "screen:comp",
                "screen",
                "Comprobantes eléctronicos emitidos",
            ),
            _candidate(
                "event:next",
                "event",
                "Siguiente página",
            ),
        ),
    )

    assert resolution.primary_canonical_id is None

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("screen:comp", "event:next"),
        candidate_types={
            "screen:comp": "screen",
            "event:next": "event",
        },
        graph_limit=20,
    )

    assert plan.enabled is True
    assert plan.strategy == "navigation_event"
    assert plan.seed_canonical_ids == ("event:next",)
    assert plan.seed_entity_types == ("event",)
    assert plan.max_hops == 2
    assert plan.limit == 64


def test_navigation_event_can_anchor_screen_scoped_control_when_no_event_exists():
    query_plan = QueryPlanner().plan(
        '¿Cómo avanzo a la siguiente página aquí? Referencia contextual validada: '
        'pantalla "Comprobantes eléctronicos emitidos".'
    )
    resolution = EntityResolution(
        query=query_plan.question,
        normalized_query=query_plan.normalized_question,
        candidates=(
            _candidate(
                "screen:comp",
                "screen",
                "Comprobantes eléctronicos emitidos",
            ),
            _candidate(
                "control:next",
                "control",
                "Siguiente página",
            ),
        ),
    )

    plan = QueryAwareGraphExpansionPlanner().plan(
        query_plan,
        resolution,
        _fused("screen:comp", "control:next"),
        candidate_types={
            "screen:comp": "screen",
            "control:next": "control",
        },
        graph_limit=20,
    )

    assert plan.enabled is True
    assert plan.strategy == "navigation_event"
    assert plan.seed_canonical_ids == ("control:next",)
    assert plan.seed_entity_types == ("control",)
    assert "HAS_CONTROL" in plan.relationships
    assert "control" in plan.endpoint_entity_types
