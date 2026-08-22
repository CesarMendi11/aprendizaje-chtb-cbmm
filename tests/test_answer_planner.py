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


def test_locate_screen_recognizes_common_module_question():
    planner = StructuralAnswerPlanner()
    relations = [
        {
            "relationship_type": "HAS_SCREEN",
            "source_canonical_id": "module:cxp",
            "target_canonical_id": "screen:list",
            "source_label": "Cuentas por cobrar",
            "target_label": "Lista de facturas",
        }
    ]

    result = planner.plan(
        "¿En qué módulo está Lista de facturas?",
        [],
        relations,
        [],
    )

    assert result["supported"] is True
    assert result["intent"] == "LOCATE_SCREEN"
    assert result["confidence"] == "high"
    assert result["answer"] == (
        'La pantalla "Lista de facturas" está dentro del módulo "Cuentas por cobrar".'
    )



def test_locate_screen_prefers_module_over_erp_root_relation():
    planner = StructuralAnswerPlanner()
    relations = [
        {
            "relationship_type": "HAS_SCREEN",
            "source_canonical_id": "erp:cbmm",
            "target_canonical_id": "screen:list",
            "source_label": "ERP Cuerpo de Bomberos Municipal de Machala",
            "target_label": "Lista de facturas",
            "source_type": "erp_system",
            "target_type": "screen",
        },
        {
            "relationship_type": "HAS_SCREEN",
            "source_canonical_id": "module:cxp",
            "target_canonical_id": "screen:list",
            "source_label": "Cuentas por cobrar",
            "target_label": "Lista de facturas",
            "source_type": "module",
            "target_type": "screen",
        },
    ]

    result = planner.plan(
        "¿En qué módulo está Lista de facturas?",
        [],
        relations,
        [],
    )

    assert result["supported"] is True
    assert result["intent"] == "LOCATE_SCREEN"
    assert result["answer"] == (
        'La pantalla "Lista de facturas" está dentro del módulo "Cuentas por cobrar".'
    )

def test_list_columns_names_the_screen_when_table_has_no_safe_name():
    planner = StructuralAnswerPlanner()
    sources = [
        {
            "canonical_id": "screen:puntos",
            "entity_type": "screen",
            "safe_label": "Puntos de emisión",
        }
    ]
    relations = [
        {
            "relationship_type": "HAS_TABLE",
            "source_canonical_id": "screen:puntos",
            "target_canonical_id": "table:puntos",
            "source_label": "Puntos de emisión",
            "target_label": "Entidad validada",
        },
        {
            "relationship_type": "HAS_COLUMN",
            "source_canonical_id": "table:puntos",
            "target_canonical_id": "column:codigo",
            "source_label": "Entidad validada",
            "target_label": "CODIGO",
        },
        {
            "relationship_type": "HAS_COLUMN",
            "source_canonical_id": "table:puntos",
            "target_canonical_id": "column:secuencial",
            "source_label": "Entidad validada",
            "target_label": "SECUENCIAL",
        },
    ]

    result = planner.plan(
        "¿Qué columnas tiene Puntos de emisión?",
        sources,
        relations,
        sources,
    )

    assert result["supported"] is True
    assert result["intent"] == "LIST_COLUMNS"
    assert 'tabla de la pantalla "Puntos de emisión"' in result["answer"]
    assert "CODIGO" in result["answer"]
    assert "SECUENCIAL" in result["answer"]
    assert 'tabla "Entidad validada"' not in result["answer"]


def test_search_control_display_removes_extractor_prefix():
    planner = StructuralAnswerPlanner()
    relations = [
        {
            "relationship_type": "HAS_FIELD",
            "source_canonical_id": "screen:list",
            "target_canonical_id": "field:ruc",
            "source_label": "Lista de facturas",
            "target_label": "RUC",
        },
        {
            "relationship_type": "HAS_CONTROL",
            "source_canonical_id": "screen:list",
            "target_canonical_id": "control:buscar",
            "source_label": "Lista de facturas",
            "target_label": "search Buscar",
        },
    ]

    result = planner.plan(
        "¿Puedo buscar una factura por RUC?",
        [],
        relations,
        [],
    )

    assert result["supported"] is True
    assert 'control "Buscar"' in result["answer"]
    assert "search Buscar" not in result["answer"]


def test_answer_planner_consumes_explicit_query_plan_instead_of_reparsing_question():
    from src.hybrid.query_plan import QueryIntent, QueryPlan

    planner = StructuralAnswerPlanner()
    query_plan = QueryPlan(
        question="texto deliberadamente neutro",
        normalized_question="texto deliberadamente neutro",
        intent=QueryIntent.LIST_FIELDS,
        target_entity_types=("screen", "field", "control"),
        requires_entity_resolution=True,
        requires_graph_context=True,
        requires_semantic_evidence=False,
        mutative_action=False,
    )
    relations = [
        {
            "relationship_type": "HAS_FIELD",
            "source_canonical_id": "screen:retenciones",
            "target_canonical_id": "field:ruc",
            "source_label": "Retenciones",
            "target_label": "RUC",
        }
    ]

    result = planner.plan(
        "texto deliberadamente neutro",
        [{"canonical_id": "screen:retenciones", "entity_type": "screen", "safe_label": "Retenciones"}],
        relations,
        [],
        query_plan=query_plan,
    )

    assert result["supported"] is True
    assert result["intent"] == QueryIntent.LIST_FIELDS
    assert "RUC" in result["answer"]
