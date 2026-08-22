from src.hybrid.evidence_selector import EvidenceSelector
from src.hybrid.entity_resolver import EntityResolution, EntityResolutionCandidate
from src.hybrid.graph_expansion import GraphExpansionPlan
from src.hybrid.query_plan import QueryIntent, QueryPlan


def plan(intent):
    return QueryPlan(
        question="q",
        normalized_question="q",
        intent=intent,
        target_entity_types=(),
        requires_entity_resolution=True,
        requires_graph_context=intent != QueryIntent.SCREEN_PURPOSE,
        requires_semantic_evidence=intent == QueryIntent.SCREEN_PURPOSE,
        mutative_action=intent == QueryIntent.MUTATIVE_ACTION,
    )


def graph_plan(*seeds):
    return GraphExpansionPlan(
        enabled=bool(seeds),
        strategy="test",
        reason="test",
        seed_canonical_ids=tuple(seeds),
        seed_entity_types=(),
        endpoint_entity_types=(),
        relationships=(),
        max_hops=2,
        limit=20,
    )


def resolved(*candidates):
    return EntityResolution(
        query="q",
        normalized_query="q",
        candidates=tuple(candidates),
    )


def candidate(canonical_id, entity_type, label, *, score=1.0):
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


def test_locate_screen_keeps_only_screen_and_owning_module_relation():
    selector = EvidenceSelector()
    resolution = resolved(candidate("screen:ano", "screen", "Año"))
    sources = [
        {"canonical_id": "screen:ano", "entity_type": "screen", "safe_label": "Año"},
        {"canonical_id": "module:general", "entity_type": "module", "safe_label": "General"},
        {"canonical_id": "control:noise", "entity_type": "control", "safe_label": "Ruido"},
    ]
    relations = [
        {
            "source_canonical_id": "module:general",
            "target_canonical_id": "screen:ano",
            "relationship_type": "HAS_SCREEN",
            "source_type": "module",
            "target_type": "screen",
            "source_label": "General",
            "target_label": "Año",
        },
        {
            "source_canonical_id": "screen:ano",
            "target_canonical_id": "control:noise",
            "relationship_type": "HAS_CONTROL",
            "source_type": "screen",
            "target_type": "control",
            "source_label": "Año",
            "target_label": "Ruido",
        },
    ]

    result = selector.select(
        plan(QueryIntent.LOCATE_SCREEN),
        resolution,
        graph_plan("screen:ano"),
        sources,
        relations,
        [],
    )

    assert result.status == "selected"
    assert result.reason == "locate_screen"
    assert [row["canonical_id"] for row in result.sources] == [
        "screen:ano",
        "module:general",
    ]
    assert [row["relationship_type"] for row in result.relations] == ["HAS_SCREEN"]


def test_list_columns_drops_unrelated_control_and_keeps_table_chain():
    selector = EvidenceSelector()
    resolution = resolved(candidate("screen:ano", "screen", "Año"))
    sources = [
        {"canonical_id": "screen:ano", "entity_type": "screen", "safe_label": "Año"},
        {"canonical_id": "table:ano", "entity_type": "table", "safe_label": "Años"},
        {"canonical_id": "column:codigo", "entity_type": "table_column", "safe_label": "CODIGO"},
        {"canonical_id": "control:noise", "entity_type": "control", "safe_label": "Ruido"},
    ]
    relations = [
        {
            "source_canonical_id": "screen:ano",
            "target_canonical_id": "table:ano",
            "relationship_type": "HAS_TABLE",
        },
        {
            "source_canonical_id": "table:ano",
            "target_canonical_id": "column:codigo",
            "relationship_type": "HAS_COLUMN",
        },
        {
            "source_canonical_id": "screen:ano",
            "target_canonical_id": "control:noise",
            "relationship_type": "HAS_CONTROL",
        },
    ]

    result = selector.select(
        plan(QueryIntent.LIST_COLUMNS),
        resolution,
        graph_plan("screen:ano"),
        sources,
        relations,
        [],
    )

    assert [row["canonical_id"] for row in result.sources] == [
        "screen:ano",
        "table:ano",
        "column:codigo",
    ]
    assert [row["relationship_type"] for row in result.relations] == [
        "HAS_TABLE",
        "HAS_COLUMN",
    ]


def test_screen_purpose_keeps_only_semantic_for_focal_screen():
    selector = EvidenceSelector()
    resolution = resolved(candidate("screen:ano", "screen", "Año"))
    sources = [
        {"canonical_id": "screen:ano", "entity_type": "screen", "safe_label": "Año"},
        {"canonical_id": "screen:otra", "entity_type": "screen", "safe_label": "Otra"},
    ]
    semantics = [
        {"semantic_id": "semantic:ano", "screen_id": "screen:ano", "safe_label": "Año"},
        {"semantic_id": "semantic:otra", "screen_id": "screen:otra", "safe_label": "Otra"},
    ]

    result = selector.select(
        plan(QueryIntent.SCREEN_PURPOSE),
        resolution,
        graph_plan(),
        sources,
        [],
        semantics,
    )

    assert [row["canonical_id"] for row in result.sources] == ["screen:ano"]
    assert [row["semantic_id"] for row in result.approved_semantics] == ["semantic:ano"]


def test_ambiguity_becomes_clarification_boundary_not_answer_context():
    selector = EvidenceSelector()
    resolution = resolved(
        candidate("field:ruc-1", "field", "RUC", score=0.99),
        candidate("field:ruc-2", "field", "RUC", score=0.99),
    )

    result = selector.select(
        plan(QueryIntent.LOCATE_FIELD),
        resolution,
        graph_plan(),
        [{"canonical_id": "screen:noise", "entity_type": "screen", "safe_label": "Ruido"}],
        [],
        [],
    )

    assert result.status == "clarification_required"
    assert result.reason == "entity_resolution_ambiguous"
    assert result.sources == ()
    assert result.relations == ()
    assert result.approved_semantics == ()
    assert {row["canonical_id"] for row in result.clarification_candidates} == {
        "field:ruc-1",
        "field:ruc-2",
    }
