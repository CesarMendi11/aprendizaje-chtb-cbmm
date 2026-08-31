from erp_assistant.retrieval.context_builder import EvidenceContextBuilder
from erp_assistant.retrieval.evidence_selector import EvidenceSelection
from erp_assistant.retrieval.query_plan import QueryIntent, QueryPlan


def plan(intent):
    return QueryPlan(
        question="q",
        normalized_question="q",
        intent=intent,
        target_entity_types=(),
        requires_entity_resolution=True,
        requires_graph_context=True,
        requires_semantic_evidence=False,
        mutative_action=False,
    )


def test_context_contains_only_selected_route_relation_and_no_noise():
    selection = EvidenceSelection(
        status="selected",
        reason="locate_screen",
        focal_canonical_ids=("screen:ano",),
        sources=(
            {
                "canonical_id": "screen:ano",
                "entity_type": "screen",
                "safe_label": "Año",
                "screen_route": "/admin/general/anios",
            },
            {
                "canonical_id": "module:general",
                "entity_type": "module",
                "safe_label": "General",
                "screen_route": None,
            },
        ),
        relations=(
            {
                "relationship_type": "HAS_SCREEN",
                "source_label": "General",
                "target_label": "Año",
                "source_type": "module",
            },
        ),
        approved_semantics=(),
    )

    context = EvidenceContextBuilder().build(plan(QueryIntent.LOCATE_SCREEN), selection)

    assert "Año" in context
    assert "/admin/general/anios" in context
    assert 'módulo "General" contiene la pantalla "Año"' in context
    assert "Ruido" not in context


def test_context_uses_only_selected_approved_semantic_payload():
    selection = EvidenceSelection(
        status="selected",
        reason="screen_purpose",
        focal_canonical_ids=("screen:ano",),
        sources=(
            {
                "canonical_id": "screen:ano",
                "entity_type": "screen",
                "safe_label": "Año",
                "screen_route": "/admin/general/anios",
            },
        ),
        relations=(),
        approved_semantics=(
            {
                "semantic_id": "semantic:ano",
                "safe_label": "Año",
                "purpose_summary": "Permite administrar los años del sistema.",
                "supported_capabilities": ["Consultar años registrados."],
            },
        ),
    )

    context = EvidenceContextBuilder().build(plan(QueryIntent.SCREEN_PURPOSE), selection)

    assert "Permite administrar los años del sistema." in context
    assert "Consultar años registrados." in context


def test_clarification_selection_produces_no_llm_context():
    selection = EvidenceSelection(
        status="clarification_required",
        reason="entity_resolution_ambiguous",
        focal_canonical_ids=(),
        sources=(),
        relations=(),
        approved_semantics=(),
        clarification_candidates=(
            {"canonical_id": "field:ruc-1", "safe_label": "RUC"},
            {"canonical_id": "field:ruc-2", "safe_label": "RUC"},
        ),
    )

    assert EvidenceContextBuilder().build(plan(QueryIntent.LOCATE_FIELD), selection) == ""
