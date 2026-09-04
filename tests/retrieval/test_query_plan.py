from erp_assistant.retrieval.query_plan import QueryIntent, QueryPlan, QueryPlanner


def test_query_planner_preserves_current_intent_contract():
    planner = QueryPlanner()

    cases = {
        "¿Para qué sirve la pantalla Año?": QueryIntent.SCREEN_PURPOSE,
        "¿Cómo buscar por RUC?": QueryIntent.SEARCH_BY_FIELD,
        "¿Qué campos tiene Retenciones?": QueryIntent.LIST_FIELDS,
        # Preserve the frozen planner precedence for M3.1; intent quality is
        # improved in later resolver/planner work without mixing concerns here.
        "¿Dónde aparece el campo RUC?": QueryIntent.LIST_FIELDS,
        "¿En qué módulo está la pantalla Año?": QueryIntent.LOCATE_SCREEN,
        "¿Dónde configuro los años?": QueryIntent.LOCATE_SCREEN,
        "¿Dónde está anoo?": QueryIntent.LOCATE_SCREEN,
        "¿Dónde está el botón Imprimir?": QueryIntent.FIND_CONTROL,
        "¿Dónde está el botón Buscar aquí?": QueryIntent.FIND_CONTROL,
        "¿Qué columnas tiene la tabla?": QueryIntent.LIST_COLUMNS,
        "¿Cómo avanzo a la siguiente página?": QueryIntent.NAVIGATION_EVENT,
        "Quiero crear un año": QueryIntent.MUTATIVE_ACTION,
    }

    for question, expected in cases.items():
        assert planner.plan(question).intent == expected


def test_query_planner_does_not_treat_descriptive_module_mentions_as_location_requests():
    planner = QueryPlanner()

    assert (
        planner.plan(
            "Cuéntame qué información y acciones se observan en Modulo de Cajas."
        ).intent
        is None
    )
    assert planner.plan("¿Qué módulo contiene la pantalla Año?").intent == QueryIntent.LOCATE_SCREEN


def test_query_plan_exposes_downstream_requirements_without_resolving_entities():
    planner = QueryPlanner()

    purpose = planner.plan("¿Para qué sirve Año?")
    assert purpose.target_entity_types == ("screen",)
    assert purpose.requires_entity_resolution is True
    assert purpose.requires_semantic_evidence is True
    assert purpose.requires_graph_context is False
    assert purpose.mutative_action is False

    columns = planner.plan("¿Qué columnas tiene Año?")
    assert columns.target_entity_types == ("screen", "table", "table_column")
    assert columns.requires_semantic_evidence is False
    assert columns.requires_graph_context is True


def test_query_plan_keeps_unknown_natural_language_as_a_valid_contract():
    plan = QueryPlanner().plan("Explícame esto con lo que sepas del ERP")

    assert isinstance(plan, QueryPlan)
    assert plan.intent is None
    assert plan.target_entity_types == ()
    assert plan.requires_entity_resolution is True
    assert plan.requires_graph_context is True
    assert plan.requires_semantic_evidence is False


def test_query_plan_normalization_is_accent_and_punctuation_stable():
    plan = QueryPlanner().plan("  ¿DÓNDE está AÑO?  ")

    assert plan.question == "¿DÓNDE está AÑO?"
    assert plan.normalized_question == "donde esta ano"


def test_query_plan_serialization_uses_stable_primitive_values():
    payload = QueryPlanner().plan("Quiero registrar un año").as_dict()

    assert payload["intent"] == "MUTATIVE_ACTION"
    assert payload["mutative_action"] is True
    assert payload["target_entity_types"] == ["screen", "control", "event"]


def test_navigation_event_query_plan_includes_visible_controls_as_resolvable_targets():
    plan = QueryPlanner().plan("¿Cómo avanzo a la siguiente página aquí?")

    assert plan.intent == QueryIntent.NAVIGATION_EVENT
    assert plan.target_entity_types == (
        "screen",
        "control",
        "ui_state",
        "event",
        "transition",
    )
