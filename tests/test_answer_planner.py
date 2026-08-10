from src.hybrid.answer_planner import StructuralAnswerPlanner


def test_list_fields_and_locate_field():
    p = StructuralAnswerPlanner({"identificacion tributaria": ["ruc"]})
    sources = []
    rels = [
        {
            "relationship_type": "HAS_FIELD",
            "source_canonical_id": "s",
            "target_canonical_id": "f",
            "source_label": "Products",
            "target_label": "SKU",
        },
        {
            "relationship_type": "HAS_CONTROL",
            "source_canonical_id": "s",
            "target_canonical_id": "c",
            "source_label": "Products",
            "target_label": "Search",
        },
        {
            "relationship_type": "HAS_SCREEN",
            "source_canonical_id": "m",
            "target_canonical_id": "s",
            "source_label": "Inventory",
            "target_label": "Products",
        },
    ]
    result = p.plan("¿Qué campos puedo usar?", sources, rels, [])
    assert result["supported"] and "SKU" in result["answer"] and "Search" in result["answer"]
    result = p.plan("¿Dónde ingreso la identificación tributaria?", sources, rels, [])
    assert result["supported"] is False


def test_mutative_compatibility():
    p = StructuralAnswerPlanner()
    rels = [
        {
            "relationship_type": "HAS_CONTROL",
            "source_canonical_id": "s",
            "target_canonical_id": "c",
            "source_label": "Products",
            "target_label": "New product",
        }
    ]
    assert p.plan("¿Cómo creo un producto?", [], rels, [])["supported"]
    assert not p.plan("¿Cómo elimino un producto?", [], rels, [])["supported"]


def test_search_by_field_uses_validated_field_and_search_control():
    planner = StructuralAnswerPlanner()

    relations = [
        {
            "relationship_type": "HAS_FIELD",
            "source_canonical_id": "screen:products",
            "target_canonical_id": "field:sku",
            "source_label": "Products",
            "target_label": "SKU",
        },
        {
            "relationship_type": "HAS_CONTROL",
            "source_canonical_id": "screen:products",
            "target_canonical_id": "control:search",
            "source_label": "Products",
            "target_label": "Search",
        },
    ]

    result = planner.plan(
        "¿Cómo puedo buscar un producto por SKU?",
        [],
        relations,
        [],
    )

    assert result["supported"] is True
    assert result["intent"] == "SEARCH_BY_FIELD"
    assert result["confidence"] == "high"
    assert "Products" in result["answer"]
    assert "SKU" in result["answer"]
    assert "Search" in result["answer"]


def test_screen_purpose_uses_only_human_approved_semantic_payload():
    planner = StructuralAnswerPlanner()
    approved = [
        {
            "semantic_id": "semantic:retenciones-purpose",
            "screen_id": "screen:retenciones",
            "safe_label": "Retenciones",
            "purpose_summary": "Permite buscar y consultar retenciones.",
            "evidence_ids": ["evidence:screen"],
        }
    ]

    result = planner.plan(
        "¿Para qué sirve la pantalla Retenciones?",
        [{"canonical_id": "screen:retenciones", "entity_type": "screen", "safe_label": "Retenciones"}],
        [],
        [],
        approved_semantics=approved,
    )

    assert result == {
        "supported": True,
        "intent": "SCREEN_PURPOSE",
        "answer": "Permite buscar y consultar retenciones.",
        "evidence_ids": [
            "semantic:retenciones-purpose",
            "screen:retenciones",
            "evidence:screen",
        ],
        "confidence": "high",
        "answer_mode": "deterministic_semantic",
    }


def test_screen_purpose_does_not_use_semantics_for_an_unmentioned_screen():
    planner = StructuralAnswerPlanner()
    result = planner.plan(
        "¿Para qué sirve la pantalla Personas?",
        [],
        [],
        [],
        approved_semantics=[
            {
                "semantic_id": "semantic:retenciones-purpose",
                "screen_id": "screen:retenciones",
                "safe_label": "Retenciones",
                "purpose_summary": "Permite buscar y consultar retenciones.",
                "evidence_ids": [],
            }
        ],
    )

    assert result["supported"] is False
    assert result["intent"] == "SCREEN_PURPOSE"
