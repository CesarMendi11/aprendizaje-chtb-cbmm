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
