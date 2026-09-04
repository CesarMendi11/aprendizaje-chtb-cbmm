from erp_assistant.retrieval.retriever import ABSTAIN, HybridKnowledgeRetriever


class Generator:
    def __init__(self):
        self.prompt = None

    def generate(self, prompt, *, system):
        self.prompt = prompt
        return "respuesta"


def test_prompt_contains_question_and_context():
    gen = Generator()
    retriever = HybridKnowledgeRetriever(
        None, chroma=None, neo4j=None, embeddings=None, generator=gen
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [{"canonical_id": "x"}],
        "context": "ENTIDADES VALIDADAS\n- field: Código\nRELACIONES VALIDADAS\n",
    }
    result = retriever.ask("¿Qué campo?", erp_id="synthetic")
    assert result["answer"] == "respuesta"
    assert "¿Qué campo?" in gen.prompt and "Código" in gen.prompt
    assert ABSTAIN in gen.prompt


def test_grounded_generator_marks_successful_answer_as_ollama_grounded():
    gen = Generator()
    retriever = HybridKnowledgeRetriever(
        None, chroma=None, neo4j=None, embeddings=None, generator=gen
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:retenciones",
                "entity_type": "screen",
                "safe_label": "Retenciones",
                "screen_route": "/admin/cuentasxcobrar/retenciones",
            }
        ],
        "relations": [],
        "approved_semantics": [],
        "context": (
            "ENTIDADES VALIDADAS\n"
            "- screen: Retenciones\n"
            "- field: RUC\n"
            "- control: Buscar\n"
            "RELACIONES VALIDADAS\n"
        ),
    }

    result = retriever.ask(
        "Explícame qué información está disponible en Retenciones y cómo se relacionan sus elementos."
    )

    assert result["answer"] == "respuesta"
    assert result["answer_mode"] == "ollama_grounded"
    assert gen.prompt is not None
    assert "Retenciones" in gen.prompt


def test_grounded_generator_exact_abstention_stays_insufficient_evidence():
    class AbstainingGenerator:
        def generate(self, prompt, *, system):
            return ABSTAIN

    retriever = HybridKnowledgeRetriever(
        None,
        chroma=None,
        neo4j=None,
        embeddings=None,
        generator=AbstainingGenerator(),
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:retenciones",
                "entity_type": "screen",
                "safe_label": "Retenciones",
                "screen_route": "/admin/cuentasxcobrar/retenciones",
            }
        ],
        "relations": [],
        "approved_semantics": [],
        "context": "ENTIDADES VALIDADAS\n- screen: Retenciones",
    }

    result = retriever.ask("Dime algo no respaldado por el contexto")

    assert result["answer"] == ABSTAIN
    assert result["answer_mode"] == "insufficient_evidence"


def test_expansion_is_bidirectional_read_only_and_parameterized():
    class Graph:
        def __init__(self):
            self.query = None
            self.parameters = None

        def execute(self, query, parameters):
            self.query, self.parameters = query, parameters
            return []

    graph = Graph()
    retriever = HybridKnowledgeRetriever(None, chroma=None, neo4j=graph, embeddings=None)
    retriever._expand(
        ["field:1"],
        "erp:s",
        "v1",
        5,
        relationships=("HAS_FIELD", "HAS_SCREEN"),
        endpoint_entity_types=("field", "screen", "module"),
        max_hops=2,
    )
    assert "[*1..3]-(b)" in graph.query
    assert "length(p) <= $max_hops" in graph.query
    assert "b.entity_type IN $endpoint_types" in graph.query
    assert "relationships(p)" in graph.query
    assert "WRITE" not in graph.query.upper()
    assert set(graph.parameters["rels"]) == {"HAS_FIELD", "HAS_SCREEN"}
    assert graph.parameters["endpoint_types"] == ["field", "screen", "module"]
    assert graph.parameters["max_hops"] == 2
    assert graph.parameters["erp_id"] == "erp:s"


def test_no_sources_abstains_without_generator():
    retriever = HybridKnowledgeRetriever(None, chroma=None, neo4j=None, embeddings=None)
    retriever.retrieve = lambda question, **kwargs: {"context": "", "sources": [], "status": "ok"}
    assert retriever.ask("¿Cómo borrar?")["answer"] == ABSTAIN


def test_no_generate_preserves_context_and_does_not_call_generator():
    gen = Generator()
    retriever = HybridKnowledgeRetriever(
        None, chroma=None, neo4j=None, embeddings=None, generator=gen
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "sources": [{"entity_type": "screen", "safe_label": "Products"}],
        "context": "ENTIDADES VALIDADAS\nProducts\nRELACIONES VALIDADAS\nProducts contiene SKU",
    }
    result = retriever.ask("¿Qué campo?", generate=False)
    assert result["answer"] is None
    assert "Products" in result["context"] and "contiene SKU" in result["context"]
    assert gen.prompt is None


def test_candidate_ids_include_intermediate_graph_path_nodes():
    neighbors = [
        {
            "canonical_id": "control:buscar",
            "path_edges": [
                {
                    "relationship_type": "HAS_FIELD",
                    "from_canonical_id": "screen:retenciones",
                    "to_canonical_id": "field:ruc",
                },
                {
                    "relationship_type": "HAS_CONTROL",
                    "from_canonical_id": "screen:retenciones",
                    "to_canonical_id": "control:buscar",
                },
            ],
        }
    ]

    result = HybridKnowledgeRetriever._candidate_ids(
        ["field:ruc"],
        neighbors,
    )

    assert result == [
        "field:ruc",
        "control:buscar",
        "screen:retenciones",
    ]


def test_validate_queries_requested_ids_without_global_item_limit():
    class Item:
        def __init__(self, canonical_id):
            self.canonical_id = canonical_id

    class Session:
        def __init__(self):
            self.statement = None

        def scalars(self, statement):
            self.statement = statement
            return [
                Item("screen:retenciones"),
                Item("field:ruc"),
            ]

    session = Session()

    retriever = HybridKnowledgeRetriever(
        session,
        chroma=None,
        neo4j=None,
        embeddings=None,
    )

    result = retriever._validate(
        ["field:ruc", "screen:retenciones"],
        "00000000-0000-0000-0000-000000000001",
    )

    sql = str(session.statement).upper()

    assert "CANONICAL_ID IN" in sql
    assert "CURRENT_REVIEW_STATUS IN" in sql
    assert "LIMIT" not in sql

    assert [item.canonical_id for item in result] == [
        "field:ruc",
        "screen:retenciones",
    ]


def test_semantic_purpose_answer_is_deterministic_and_skips_generator():
    gen = Generator()
    retriever = HybridKnowledgeRetriever(
        None, chroma=None, neo4j=None, embeddings=None, generator=gen
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:retenciones",
                "entity_type": "screen",
                "safe_label": "Retenciones",
                "screen_route": "/admin/cuentasxcobrar/retenciones",
            }
        ],
        "relations": [],
        "approved_semantics": [
            {
                "semantic_id": "semantic:retenciones-purpose",
                "screen_id": "screen:retenciones",
                "safe_label": "Retenciones",
                "purpose_summary": "Permite buscar y consultar retenciones.",
                "evidence_ids": ["evidence:screen"],
            }
        ],
        "context": "SEMÁNTICA HUMANA APROBADA",
    }

    result = retriever.ask("¿Para qué sirve la pantalla Retenciones?")

    assert result["answer"] == "Permite buscar y consultar retenciones."
    assert result["answer_mode"] == "deterministic_semantic"
    assert result["intent"] == "SCREEN_PURPOSE"
    assert result["confidence"] == "high"
    assert gen.prompt is None


def test_screen_purpose_uses_reauthorized_semantics_without_graph_expansion(monkeypatch):
    from types import SimpleNamespace

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(self, embedding, **kwargs):
            return []

    class SemanticChroma:
        def query(self, embedding, **kwargs):
            return [
                {
                    "semantic_id": "semantic:untrusted-hit",
                    "screen_id": "screen:untrusted",
                    "canonical_id": "screen:untrusted",
                }
            ]

    class Authorizer:
        def __init__(self):
            self.hits = None

        def authorize_hits(self, hits, *, version):
            self.hits = hits
            return [
                {
                    "semantic_id": "semantic:retenciones-purpose",
                    "semantic_type": "screen_purpose",
                    "screen_id": "screen:retenciones",
                    "canonical_id": "screen:retenciones",
                    "safe_label": "Retenciones",
                    "screen_route": "/admin/cuentasxcobrar/retenciones",
                    "review_status": "approved",
                    "review_revision": 1,
                    "evidence_hash": "e" * 64,
                    "evidence_ids": ["evidence:screen"],
                    "purpose_summary": "Permite buscar y consultar retenciones.",
                    "supported_capabilities": [],
                    "score": 0.95,
                }
            ]

    class Graph:
        def __init__(self):
            self.parameters = None

        def execute(self, query, parameters):
            self.parameters = parameters
            return []

    authorizer = Authorizer()
    graph = Graph()
    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        semantic_chroma=SemanticChroma(),
        semantic_authorizer=authorizer,
        neo4j=graph,
        embeddings=Embeddings(),
    )
    item = SimpleNamespace(
        id="screen-db-id",
        canonical_id="screen:retenciones",
        entity_type="screen",
        route="/admin/cuentasxcobrar/retenciones",
    )
    retriever._validate = lambda ids, version_id: [item] if "screen:retenciones" in ids else []
    retriever._effective = lambda item_id: {"title": "Retenciones"}

    result = retriever.retrieve("¿Para qué sirve la pantalla Retenciones?")

    assert authorizer.hits[0]["screen_id"] == "screen:untrusted"
    assert graph.parameters is None
    assert result["graph_expansion"]["enabled"] is False
    assert result["graph_expansion"]["reason"] == "query_plan_no_graph_context"
    assert result["retrieval"]["semantic_candidates"] == 1
    assert result["retrieval"]["approved_semantic_hits"] == 1
    assert result["sources"][0]["origin"] == "approved_semantic"
    assert result["approved_semantics"][0]["semantic_id"] == "semantic:retenciones-purpose"


def test_grounded_generator_paraphrased_abstention_stays_insufficient_evidence():
    class ParaphrasingAbstentionGenerator:
        def generate(self, prompt, *, system):
            return (
                "No encontré conocimiento validado suficiente para determinar "
                'los campos de la pantalla "Comprobantes electrónicos emitidos".'
            )

    retriever = HybridKnowledgeRetriever(
        None,
        chroma=None,
        neo4j=None,
        embeddings=None,
        generator=ParaphrasingAbstentionGenerator(),
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:comprobantes",
                "entity_type": "screen",
                "safe_label": "Comprobantes electrónicos emitidos",
                "screen_route": "/admin/cuentasxcobrar/comprobantes",
            }
        ],
        "relations": [],
        "approved_semantics": [],
        "context": "ENTIDADES VALIDADAS\n- screen: Comprobantes electrónicos emitidos",
    }

    result = retriever.ask("¿Qué campos tiene Comprobantes electrónicos emitidos?")

    assert result["answer"].startswith("No encontré conocimiento validado suficiente")
    assert result["answer_mode"] == "insufficient_evidence"


def test_list_columns_uses_query_aware_three_hop_graph_from_ui_state(monkeypatch):
    from types import SimpleNamespace

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(self, embedding, **kwargs):
            # Simulates the observed case where a UI state crowds out the screen
            # itself from structural dense retrieval.
            return [
                {
                    "canonical_id": "ui_state:comp",
                    "entity_type": "ui_state",
                    "safe_label": "Comprobantes electrónicos emitidos",
                    "score": 0.7,
                }
            ]

    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        neo4j=object(),
        embeddings=Embeddings(),
    )

    items = {
        "ui_state:comp": SimpleNamespace(
            id="db-state",
            canonical_id="ui_state:comp",
            entity_type="ui_state",
            route=None,
        ),
        "screen:comp": SimpleNamespace(
            id="db-screen",
            canonical_id="screen:comp",
            entity_type="screen",
            route="/admin/cuentasxcobrar/comprobantes",
        ),
        "table:comp": SimpleNamespace(
            id="db-table",
            canonical_id="table:comp",
            entity_type="table",
            route=None,
        ),
        "column:correo": SimpleNamespace(
            id="db-column",
            canonical_id="column:correo",
            entity_type="table_column",
            route=None,
        ),
    }
    payloads = {
        "db-state": {"label": "Comprobantes electrónicos emitidos"},
        "db-screen": {"title": "Comprobantes electrónicos emitidos"},
        "db-table": {"name": None},
        "db-column": {"name": "CORREO"},
    }

    retriever._validate = lambda ids, version_id: [items[cid] for cid in ids if cid in items]
    retriever._effective = lambda item_id: payloads[item_id]

    calls = []

    def expand(
        seeds,
        erp_id,
        knowledge_version,
        limit,
        *,
        relationships,
        endpoint_entity_types,
        max_hops,
    ):
        calls.append(
            {
                "seeds": list(seeds),
                "limit": limit,
                "relationships": tuple(relationships),
                "endpoint_entity_types": tuple(endpoint_entity_types),
                "max_hops": max_hops,
            }
        )
        assert seeds == ["ui_state:comp"]
        assert limit >= 64
        assert set(relationships) == {"HAS_STATE", "HAS_TABLE", "HAS_COLUMN"}
        assert max_hops == 3
        return [
            {
                "source_canonical_id": "ui_state:comp",
                "canonical_id": "screen:comp",
                "entity_type": "screen",
                "path_edges": [
                    {
                        "relationship_type": "HAS_STATE",
                        "from_canonical_id": "screen:comp",
                        "to_canonical_id": "ui_state:comp",
                    }
                ],
            },
            {
                "source_canonical_id": "ui_state:comp",
                "canonical_id": "table:comp",
                "entity_type": "table",
                "path_edges": [
                    {
                        "relationship_type": "HAS_STATE",
                        "from_canonical_id": "screen:comp",
                        "to_canonical_id": "ui_state:comp",
                    },
                    {
                        "relationship_type": "HAS_TABLE",
                        "from_canonical_id": "screen:comp",
                        "to_canonical_id": "table:comp",
                    },
                ],
            },
            {
                "source_canonical_id": "ui_state:comp",
                "canonical_id": "column:correo",
                "entity_type": "table_column",
                "path_edges": [
                    {
                        "relationship_type": "HAS_STATE",
                        "from_canonical_id": "screen:comp",
                        "to_canonical_id": "ui_state:comp",
                    },
                    {
                        "relationship_type": "HAS_TABLE",
                        "from_canonical_id": "screen:comp",
                        "to_canonical_id": "table:comp",
                    },
                    {
                        "relationship_type": "HAS_COLUMN",
                        "from_canonical_id": "table:comp",
                        "to_canonical_id": "column:correo",
                    },
                ],
            },
        ]

    retriever._expand = expand

    result = retriever.retrieve(
        "¿Qué información aparece en la tabla de Comprobantes electrónicos emitidos?"
    )

    assert len(calls) == 1
    assert result["graph_expansion"]["strategy"] == "list_columns"
    assert result["graph_expansion"]["seed_canonical_ids"] == ["ui_state:comp"]
    assert result["graph_expansion"]["max_hops"] == 3
    assert any(r["relationship_type"] == "HAS_COLUMN" for r in result["relations"])
    column_source = next(s for s in result["sources"] if s["canonical_id"] == "column:correo")
    assert column_source["screen_route"] == "/admin/cuentasxcobrar/comprobantes"


def test_ask_builds_one_query_plan_before_retrieval_and_exposes_it():
    from erp_assistant.retrieval.query_plan import QueryIntent, QueryPlan

    class QueryPlannerSpy:
        def __init__(self):
            self.calls = []

        def plan(self, question):
            self.calls.append(question)
            return QueryPlan(
                question=question,
                normalized_question="para que sirve retenciones",
                intent=QueryIntent.SCREEN_PURPOSE,
                target_entity_types=("screen",),
                requires_entity_resolution=True,
                requires_graph_context=False,
                requires_semantic_evidence=True,
                mutative_action=False,
            )

    query_planner = QueryPlannerSpy()
    retriever = HybridKnowledgeRetriever(
        None,
        chroma=None,
        neo4j=None,
        embeddings=None,
        query_planner=query_planner,
    )
    captured = {}

    def fake_retrieve(question, **kwargs):
        captured["query_plan"] = kwargs["query_plan"]
        return {
            "status": "ok",
            "question": question,
            "sources": [],
            "relations": [],
            "approved_semantics": [],
            "context": "",
        }

    retriever.retrieve = fake_retrieve

    result = retriever.ask("¿Para qué sirve Retenciones?", generate=False)

    assert query_planner.calls == ["¿Para qué sirve Retenciones?"]
    assert captured["query_plan"].intent == QueryIntent.SCREEN_PURPOSE
    assert result["query_plan"]["intent"] == "SCREEN_PURPOSE"
    assert result["query_plan"]["requires_semantic_evidence"] is True


def test_query_aware_graph_uses_strong_screen_seed_instead_of_dense_noise(monkeypatch):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import EntityResolution, EntityResolutionCandidate

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(self, embedding, **kwargs):
            return [
                {
                    "canonical_id": "field:other",
                    "entity_type": "field",
                    "safe_label": "Otro",
                    "score": 0.8,
                }
            ]

    class Resolver:
        def resolve(self, query_plan, *, version_id, limit):
            assert version_id == "version-db-id"
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=(
                    EntityResolutionCandidate(
                        canonical_id="screen:ano",
                        entity_type="screen",
                        safe_label="Año",
                        route="/admin/general/anios",
                        score=1.0,
                        channels=("normalized_mention",),
                        matched_terms=("ano",),
                    ),
                ),
            )

    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        neo4j=object(),
        embeddings=Embeddings(),
        entity_resolver=Resolver(),
    )

    items = {
        "screen:ano": SimpleNamespace(
            id="db-screen-ano",
            canonical_id="screen:ano",
            entity_type="screen",
            route="/admin/general/anios",
        ),
        "field:other": SimpleNamespace(
            id="db-field-other",
            canonical_id="field:other",
            entity_type="field",
            route=None,
        ),
    }
    payloads = {
        "db-screen-ano": {"title": "Año"},
        "db-field-other": {"label": "Otro"},
    }
    retriever._validate = lambda ids, version_id: [items[cid] for cid in ids if cid in items]
    retriever._effective = lambda item_id: payloads[item_id]

    calls = []

    def expand(
        seeds,
        erp_id,
        knowledge_version,
        limit,
        *,
        relationships,
        endpoint_entity_types,
        max_hops,
    ):
        calls.append(
            {
                "seeds": list(seeds),
                "limit": limit,
                "relationships": tuple(relationships),
                "endpoint_entity_types": tuple(endpoint_entity_types),
                "max_hops": max_hops,
            }
        )
        return []

    retriever._expand = expand

    result = retriever.retrieve("¿Dónde configuro los años?")

    assert len(calls) == 1
    assert calls[0]["seeds"] == ["screen:ano"]
    assert set(calls[0]["relationships"]) == {
        "HAS_MODULE",
        "HAS_SUBMODULE",
        "HAS_SCREEN",
        "HAS_STATE",
    }
    assert calls[0]["max_hops"] == 2
    assert result["graph_expansion"]["strategy"] == "locate_screen"
    assert result["graph_expansion"]["seed_canonical_ids"] == ["screen:ano"]
    assert result["entity_resolution"]["primary_canonical_id"] == "screen:ano"
    assert result["retrieval"]["entity_candidates"] == 1
    assert result["rank_fusion"]["algorithm"] == "rrf"
    assert result["rank_fusion"]["channel_sizes"]["canonical"] == 1
    assert result["rank_fusion"]["channel_sizes"]["structural_dense"] == 1
    assert [candidate["canonical_id"] for candidate in result["rank_fusion"]["candidates"][:2]] == [
        "screen:ano",
        "field:other",
    ]
    source = next(row for row in result["sources"] if row["canonical_id"] == "screen:ano")
    assert source["resolution_channels"] == ["normalized_mention"]
    assert source["retrieval_channels"] == ["canonical"]
    assert source["retrieval_rank"] == 1
    assert source["rrf_score"] is not None
    assert source["score"] == 1.0
    assert result["evidence_selection"]["status"] == "selected"
    assert result["evidence_selection"]["reason"] == "locate_screen"
    assert result["evidence_selection"]["source_ids"] == ["screen:ano"]
    assert result["retrieval"]["selected_sources"] == 1
    assert "field:other" not in result["evidence_selection"]["source_ids"]


def test_rrf_does_not_promote_legitimate_ambiguous_canonical_matches_to_graph_seeds(monkeypatch):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import EntityResolution, EntityResolutionCandidate

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(self, embedding, **kwargs):
            return []

    class Resolver:
        def resolve(self, query_plan, *, version_id, limit):
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=(
                    EntityResolutionCandidate(
                        canonical_id="field:ruc-1",
                        entity_type="field",
                        safe_label="RUC",
                        route=None,
                        score=0.99,
                        channels=("alias",),
                        matched_terms=("ruc",),
                        channel_scores=(("alias", 0.99),),
                    ),
                    EntityResolutionCandidate(
                        canonical_id="field:ruc-2",
                        entity_type="field",
                        safe_label="RUC",
                        route=None,
                        score=0.99,
                        channels=("alias",),
                        matched_terms=("ruc",),
                        channel_scores=(("alias", 0.99),),
                    ),
                ),
            )

    class Graph:
        def __init__(self):
            self.parameters = None

        def execute(self, query, parameters):
            self.parameters = parameters
            return []

    graph = Graph()
    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        neo4j=graph,
        embeddings=Embeddings(),
        entity_resolver=Resolver(),
    )
    retriever._validate = lambda ids, version_id: []

    result = retriever.retrieve("¿Dónde aparece la identificación tributaria?")

    assert result["entity_resolution"]["status"] == "ambiguous"
    assert set(result["rank_fusion"]["excluded_ambiguous_canonical_ids"]) == {
        "field:ruc-1",
        "field:ruc-2",
    }
    assert graph.parameters is None
    assert result["sources"] == []
    assert result["context"] == ""
    assert result["evidence_selection"]["status"] == "clarification_required"
    assert result["evidence_selection"]["reason"] == "entity_resolution_ambiguous"
    assert {
        row["canonical_id"] for row in result["evidence_selection"]["clarification_candidates"]
    } == {"field:ruc-1", "field:ruc-2"}


def test_answer_decision_exposes_deterministic_path():
    retriever = HybridKnowledgeRetriever(None, chroma=None, neo4j=None, embeddings=None)
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:years",
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
        ],
        "relations": [
            {
                "relationship_type": "HAS_SCREEN",
                "source_canonical_id": "module:general",
                "target_canonical_id": "screen:years",
                "source_label": "General",
                "target_label": "Año",
                "source_type": "module",
                "target_type": "screen",
            }
        ],
        "approved_semantics": [],
        "context": "ENTIDADES VALIDADAS\n- screen: Año\n- module: General",
        "evidence_selection": {
            "status": "selected",
            "reason": "locate_screen",
            "clarification_candidates": [],
        },
    }

    result = retriever.ask("¿Dónde está Año?")

    assert result["answer_mode"] == "deterministic_graph"
    assert result["answer_decision"] == {
        "decision": "DETERMINISTIC_ANSWER",
        "reason": "deterministic_structural_answer",
        "intent": "LOCATE_SCREEN",
        "confidence": "high",
    }


def test_ambiguity_returns_deterministic_clarification_without_generator():
    class ForbiddenGenerator:
        def generate(self, prompt, *, system):
            raise AssertionError("clarification must not call the LLM")

    retriever = HybridKnowledgeRetriever(
        None,
        chroma=None,
        neo4j=None,
        embeddings=None,
        generator=ForbiddenGenerator(),
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [],
        "relations": [],
        "approved_semantics": [],
        "context": "",
        "evidence_selection": {
            "status": "clarification_required",
            "reason": "entity_resolution_ambiguous",
            "clarification_candidates": [
                {
                    "canonical_id": "field:ruc-a",
                    "entity_type": "field",
                    "safe_label": "RUC",
                    "route": None,
                },
                {
                    "canonical_id": "field:ruc-b",
                    "entity_type": "field",
                    "safe_label": "RUC",
                    "route": None,
                },
            ],
        },
    }

    result = retriever.ask("¿Dónde aparece la identificación tributaria?")

    assert result["answer_mode"] == "clarification"
    assert result["answer_decision"]["decision"] == "CLARIFICATION"
    assert result["answer_decision"]["reason"] == "entity_resolution_ambiguous"
    assert "RUC" in result["answer"]
    assert "field:" not in result["answer"]
    assert result["evidence_ids"] == []


def test_grounded_generation_exposes_grounded_llm_decision():
    gen = Generator()
    retriever = HybridKnowledgeRetriever(
        None, chroma=None, neo4j=None, embeddings=None, generator=gen
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:years",
                "entity_type": "screen",
                "safe_label": "Año",
                "screen_route": "/admin/general/anios",
            }
        ],
        "relations": [],
        "approved_semantics": [],
        "context": "ENTIDADES VALIDADAS\n- screen: Año",
        "evidence_selection": {
            "status": "selected",
            "reason": "bounded_generic",
            "clarification_candidates": [],
        },
    }

    result = retriever.ask("Cuéntame sobre Año")

    assert result["answer"] == "respuesta"
    assert result["answer_mode"] == "ollama_grounded"
    assert result["answer_decision"]["decision"] == "GROUNDED_LLM"
    assert result["answer_decision"]["reason"] == "grounded_context_available"


def test_generator_abstention_updates_final_answer_decision():
    class AbstainingGenerator:
        def generate(self, prompt, *, system):
            return ABSTAIN

    retriever = HybridKnowledgeRetriever(
        None,
        chroma=None,
        neo4j=None,
        embeddings=None,
        generator=AbstainingGenerator(),
    )
    retriever.retrieve = lambda question, **kwargs: {
        "status": "ok",
        "question": question,
        "sources": [
            {
                "canonical_id": "screen:years",
                "entity_type": "screen",
                "safe_label": "Año",
                "screen_route": "/admin/general/anios",
            }
        ],
        "relations": [],
        "approved_semantics": [],
        "context": "ENTIDADES VALIDADAS\n- screen: Año",
        "evidence_selection": {
            "status": "selected",
            "reason": "bounded_generic",
            "clarification_candidates": [],
        },
    }

    result = retriever.ask("Cuéntame algo no respaldado sobre Año")

    assert result["answer"] == ABSTAIN
    assert result["answer_mode"] == "insufficient_evidence"
    assert result["answer_decision"]["decision"] == "ABSTENTION"
    assert result["answer_decision"]["reason"] == "generator_abstained"


def test_unknown_question_without_canonical_anchor_fails_closed_before_dense_retrieval(monkeypatch):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import EntityResolution

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    class Resolver:
        def resolve(self, query_plan, *, version_id, limit):
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=(),
            )

    class ForbiddenEmbeddings:
        def embed(self, value):
            raise AssertionError("unknown out-of-domain query must not reach dense retrieval")

    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=None,
        neo4j=None,
        embeddings=ForbiddenEmbeddings(),
        entity_resolver=Resolver(),
    )

    result = retriever.retrieve("¿Cuál es la capital de Francia?")

    assert result["query_plan"]["intent"] is None
    assert result["sources"] == []
    assert result["relations"] == []
    assert result["approved_semantics"] == []
    assert result["context"] == ""
    assert result["evidence_selection"]["status"] == "insufficient"
    assert result["evidence_selection"]["reason"] == "insufficient_evidence"
    assert result["retrieval"]["selected_sources"] == 0
    assert result["retrieval"]["selected_relations"] == 0
    assert result["retrieval"]["selected_semantics"] == 0


def test_retrieve_resolves_version_without_preparing_chroma_projection(monkeypatch):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import EntityResolution

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

        def prepare(self, **kwargs):
            raise AssertionError("query runtime must not prepare the full Chroma projection")

    class Resolver:
        def resolve(self, query_plan, *, version_id, limit):
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=(),
            )

    class ForbiddenEmbeddings:
        def embed(self, value):
            raise AssertionError("out-of-domain query must fail before dense retrieval")

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=None,
        neo4j=None,
        embeddings=ForbiddenEmbeddings(),
        entity_resolver=Resolver(),
    )

    result = retriever.retrieve("¿Cuál es la capital de Francia?")

    assert result["knowledge_version"] == "v1"
    assert result["evidence_selection"]["reason"] == "insufficient_evidence"


def test_generic_grounded_question_scopes_ambiguous_child_label_to_explicit_screen(
    monkeypatch,
):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import (
        EntityResolution,
        EntityResolutionCandidate,
    )
    from erp_assistant.retrieval.query_plan import QueryPlan

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr(
        "erp_assistant.retrieval.retriever.ChromaSyncService",
        SyncService,
    )

    def candidate(
        canonical_id,
        entity_type,
        label,
    ):
        return EntityResolutionCandidate(
            canonical_id=canonical_id,
            entity_type=entity_type,
            safe_label=label,
            route=(
                "/rentas/cajas"
                if entity_type == "screen"
                else None
            ),
            score=1.0,
            channels=(
                "normalized_mention",
            ),
            matched_terms=(
                label.casefold(),
            ),
            channel_scores=(
                (
                    "normalized_mention",
                    1.0,
                ),
            ),
        )

    class Resolver:
        def __init__(self):
            self.scoped_calls = []

        def resolve(
            self,
            query_plan,
            *,
            version_id,
            limit,
        ):
            if (
                query_plan.target_entity_types
                == ("screen",)
            ):
                rows = (
                    candidate(
                        "screen:cajas",
                        "screen",
                        "Modulo de Cajas",
                    ),
                )
            else:
                rows = (
                    candidate(
                        "screen:cajas",
                        "screen",
                        "Modulo de Cajas",
                    ),
                    candidate(
                        "column:acciones-a",
                        "table_column",
                        "ACCIONES",
                    ),
                    candidate(
                        "column:acciones-b",
                        "table_column",
                        "ACCIONES",
                    ),
                )

            return EntityResolution(
                query=query_plan.question,
                normalized_query=(
                    query_plan.normalized_question
                ),
                candidates=rows,
            )

        def resolve_in_screen(
            self,
            query_plan,
            *,
            version_id,
            screen_id,
            limit,
        ):
            self.scoped_calls.append(
                (
                    screen_id,
                    query_plan.intent,
                )
            )

            return EntityResolution(
                query=query_plan.question,
                normalized_query=(
                    query_plan.normalized_question
                ),
                candidates=(
                    candidate(
                        "screen:cajas",
                        "screen",
                        "Modulo de Cajas",
                    ),
                    candidate(
                        "column:acciones-cajas",
                        "table_column",
                        "ACCIONES",
                    ),
                ),
            )

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(
            self,
            embedding,
            **kwargs,
        ):
            return []

    resolver = Resolver()

    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        neo4j=object(),
        embeddings=Embeddings(),
        entity_resolver=resolver,
    )

    items = {
        "screen:cajas":
            SimpleNamespace(
                id="db-screen-cajas",
                canonical_id=(
                    "screen:cajas"
                ),
                entity_type="screen",
                route="/rentas/cajas",
            ),

        "column:acciones-cajas":
            SimpleNamespace(
                id="db-column-acciones",
                canonical_id=(
                    "column:acciones-cajas"
                ),
                entity_type=(
                    "table_column"
                ),
                route=None,
            ),
    }

    payloads = {
        "db-screen-cajas": {
            "title":
                "Modulo de Cajas",
        },

        "db-column-acciones": {
            "name":
                "ACCIONES",
        },
    }

    retriever._validate = (
        lambda ids, version_id: [
            items[cid]
            for cid in ids
            if cid in items
        ]
    )

    retriever._effective = (
        lambda item_id:
            payloads[item_id]
    )

    retriever._expand = (
        lambda seeds, *args, **kwargs: []
    )

    question = (
        "Cuéntame qué información y acciones "
        "se observan en Modulo de Cajas."
    )

    query_plan = QueryPlan(
        question=question,
        normalized_question=(
            "cuentame que informacion y acciones "
            "se observan en modulo de cajas"
        ),
        intent=None,
        target_entity_types=(),
        requires_entity_resolution=True,
        requires_graph_context=True,
        requires_semantic_evidence=False,
        mutative_action=False,
    )

    result = retriever.retrieve(
        question,
        query_plan=query_plan,
    )

    assert resolver.scoped_calls == [
        (
            "screen:cajas",
            None,
        )
    ]

    assert (
        result[
            "conversation_context"
        ]["reason"]
        == "current_turn_screen_scope"
    )

    assert (
        result[
            "entity_resolution"
        ]["status"]
        == "resolved"
    )

    assert (
        result[
            "evidence_selection"
        ]["status"]
        == "selected"
    )

    assert (
        result[
            "evidence_selection"
        ]["reason"]
        == "bounded_generic"
    )


def test_same_turn_explicit_screen_scopes_ambiguous_search_before_graph(monkeypatch):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import (
        EntityResolution,
        EntityResolutionCandidate,
    )

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    def candidate(canonical_id, entity_type, label):
        return EntityResolutionCandidate(
            canonical_id=canonical_id,
            entity_type=entity_type,
            safe_label=label,
            route="/retenciones" if entity_type == "screen" else None,
            score=1.0,
            channels=("normalized_mention",),
            matched_terms=(label.casefold(),),
            channel_scores=(("normalized_mention", 1.0),),
        )

    class Resolver:
        def __init__(self):
            self.scoped_calls = []

        def resolve(self, query_plan, *, version_id, limit):
            if query_plan.target_entity_types == ("screen",):
                rows = (candidate("screen:ret", "screen", "Retenciones"),)
            else:
                rows = (
                    candidate("screen:ret", "screen", "Retenciones"),
                    candidate("field:ruc-ret", "field", "RUC"),
                    candidate("field:ruc-other", "field", "RUC"),
                )
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=rows,
            )

        def resolve_in_screen(self, query_plan, *, version_id, screen_id, limit):
            self.scoped_calls.append((screen_id, query_plan.intent))
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=(
                    candidate("screen:ret", "screen", "Retenciones"),
                    candidate("field:ruc-ret", "field", "RUC"),
                    candidate("control:buscar", "control", "Buscar"),
                ),
            )

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(self, embedding, **kwargs):
            return []

    resolver = Resolver()
    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        neo4j=object(),
        embeddings=Embeddings(),
        entity_resolver=resolver,
    )

    items = {
        "screen:ret": SimpleNamespace(
            id="db-screen-ret",
            canonical_id="screen:ret",
            entity_type="screen",
            route="/retenciones",
        ),
        "field:ruc-ret": SimpleNamespace(
            id="db-field-ruc",
            canonical_id="field:ruc-ret",
            entity_type="field",
            route=None,
        ),
        "control:buscar": SimpleNamespace(
            id="db-control-buscar",
            canonical_id="control:buscar",
            entity_type="control",
            route=None,
        ),
    }
    payloads = {
        "db-screen-ret": {"title": "Retenciones"},
        "db-field-ruc": {"label": "RUC"},
        "db-control-buscar": {"label": "Buscar"},
    }
    retriever._validate = lambda ids, version_id: [items[cid] for cid in ids if cid in items]
    retriever._effective = lambda item_id: payloads[item_id]
    retriever._expand = lambda seeds, *args, **kwargs: [
        {
            "source_canonical_id": "screen:ret",
            "canonical_id": "field:ruc-ret",
            "entity_type": "field",
            "path_edges": [
                {
                    "relationship_type": "HAS_FIELD",
                    "from_canonical_id": "screen:ret",
                    "to_canonical_id": "field:ruc-ret",
                }
            ],
        },
        {
            "source_canonical_id": "screen:ret",
            "canonical_id": "control:buscar",
            "entity_type": "control",
            "path_edges": [
                {
                    "relationship_type": "HAS_CONTROL",
                    "from_canonical_id": "screen:ret",
                    "to_canonical_id": "control:buscar",
                }
            ],
        },
    ]

    result = retriever.retrieve("¿Cómo busco por RUC en Retenciones?")

    assert resolver.scoped_calls
    assert result["conversation_context"]["reason"] == "current_turn_screen_scope"
    assert result["entity_resolution"]["status"] == "resolved"
    assert result["graph_expansion"]["enabled"] is True
    assert result["graph_expansion"]["strategy"] == "search_by_field"
    assert result["evidence_selection"]["status"] == "selected"
    assert result["evidence_selection"]["reason"] == "search_by_field"
    assert {row["relationship_type"] for row in result["relations"]} == {
        "HAS_FIELD",
        "HAS_CONTROL",
    }


def test_same_turn_explicit_screen_scopes_ambiguous_navigation_before_graph(monkeypatch):
    from types import SimpleNamespace

    from erp_assistant.retrieval.entity_resolver import (
        EntityResolution,
        EntityResolutionCandidate,
    )

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def resolve_version(self, *, erp_id=None, knowledge_version=None):
            return version

    monkeypatch.setattr("erp_assistant.retrieval.retriever.ChromaSyncService", SyncService)

    def candidate(canonical_id, entity_type, label):
        return EntityResolutionCandidate(
            canonical_id=canonical_id,
            entity_type=entity_type,
            safe_label=label,
            route="/retenciones" if entity_type == "screen" else None,
            score=1.0,
            channels=("normalized_mention",),
            matched_terms=(label.casefold(),),
            channel_scores=(("normalized_mention", 1.0),),
        )

    class Resolver:
        def __init__(self):
            self.scoped_calls = []

        def resolve(self, query_plan, *, version_id, limit):
            if query_plan.target_entity_types == ("screen",):
                rows = (candidate("screen:ret", "screen", "Retenciones"),)
            else:
                rows = (
                    candidate("control:next-a", "control", "Siguiente página"),
                    candidate("control:next-b", "control", "Siguiente página"),
                    candidate("screen:ret", "screen", "Retenciones"),
                )
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=rows,
            )

        def resolve_in_screen(self, query_plan, *, version_id, screen_id, limit):
            self.scoped_calls.append((screen_id, query_plan.intent))
            return EntityResolution(
                query=query_plan.question,
                normalized_query=query_plan.normalized_question,
                candidates=(
                    candidate("screen:ret", "screen", "Retenciones"),
                    candidate("control:next-ret", "control", "Siguiente página"),
                    candidate("event:next-ret", "event", "Siguiente página"),
                ),
            )

    class Embeddings:
        def embed(self, question):
            return [[0.1, 0.2]]

    class StructuralChroma:
        def query(self, embedding, **kwargs):
            return []

    resolver = Resolver()
    retriever = HybridKnowledgeRetriever(
        object(),
        chroma=StructuralChroma(),
        neo4j=object(),
        embeddings=Embeddings(),
        entity_resolver=resolver,
    )

    items = {
        "screen:ret": SimpleNamespace(
            id="db-screen-ret",
            canonical_id="screen:ret",
            entity_type="screen",
            route="/retenciones",
        ),
        "control:next-ret": SimpleNamespace(
            id="db-control-next",
            canonical_id="control:next-ret",
            entity_type="control",
            route=None,
        ),
        "event:next-ret": SimpleNamespace(
            id="db-event-next",
            canonical_id="event:next-ret",
            entity_type="event",
            route=None,
        ),
    }
    payloads = {
        "db-screen-ret": {"title": "Retenciones"},
        "db-control-next": {"label": "Siguiente página"},
        "db-event-next": {"label": "Siguiente página"},
    }
    retriever._validate = lambda ids, version_id: [items[cid] for cid in ids if cid in items]
    retriever._effective = lambda item_id: payloads[item_id]
    retriever._expand = lambda seeds, *args, **kwargs: [
        {
            "source_canonical_id": "event:next-ret",
            "canonical_id": "screen:ret",
            "entity_type": "screen",
            "path_edges": [
                {
                    "relationship_type": "HAS_EVENT",
                    "from_canonical_id": "screen:ret",
                    "to_canonical_id": "event:next-ret",
                }
            ],
        },
        {
            "source_canonical_id": "event:next-ret",
            "canonical_id": "control:next-ret",
            "entity_type": "control",
            "path_edges": [
                {
                    "relationship_type": "HAS_EVENT",
                    "from_canonical_id": "screen:ret",
                    "to_canonical_id": "event:next-ret",
                },
                {
                    "relationship_type": "HAS_CONTROL",
                    "from_canonical_id": "screen:ret",
                    "to_canonical_id": "control:next-ret",
                },
            ],
        },
    ]

    result = retriever.retrieve("¿Cómo avanzo a la siguiente página en Retenciones?")

    assert resolver.scoped_calls
    assert result["conversation_context"]["reason"] == "current_turn_screen_scope"
    assert result["entity_resolution"]["status"] == "resolved"
    assert result["graph_expansion"]["enabled"] is True
    assert result["graph_expansion"]["strategy"] == "navigation_event"
    assert result["evidence_selection"]["status"] == "selected"
    assert result["evidence_selection"]["reason"] == "navigation_event"
    assert any(row["relationship_type"] == "HAS_EVENT" for row in result["relations"])


def test_current_route_context_uses_governed_screen_and_replaces_stale_scope(monkeypatch):
    import uuid
    from types import SimpleNamespace

    from erp_assistant.retrieval.conversation_context import (
        ConversationEntity,
        ConversationState,
    )

    screen = SimpleNamespace(
        id=uuid.uuid4(),
        canonical_id="screen:year",
        entity_type="screen",
        route="/admin/general/anios",
    )

    class Session:
        def scalars(self, statement):
            return [screen]

    version = SimpleNamespace(
        id=uuid.uuid4(),
        erp_id="erp:test",
        knowledge_version="v-active",
    )

    retriever = HybridKnowledgeRetriever(
        Session(),
        chroma=None,
        neo4j=None,
        embeddings=None,
    )

    monkeypatch.setattr(
        retriever,
        "_effective",
        lambda item_id: {
            "title": "Año",
            "route": "/admin/general/anios",
        },
    )

    previous = ConversationState(
        erp_id="erp:test",
        knowledge_version="v-active",
        current_screen=ConversationEntity(
            canonical_id="screen:old",
            entity_type="screen",
            safe_label="Pantalla anterior",
            route="/admin/old",
        ),
        current_module=ConversationEntity(
            canonical_id="module:old",
            entity_type="module",
            safe_label="Módulo anterior",
        ),
        turn_index=7,
    )

    state = retriever._conversation_state_for_current_route(
        previous,
        "/admin/general/anios?foo=bar#section",
        version=version,
    )

    assert state.erp_id == "erp:test"
    assert state.knowledge_version == "v-active"
    assert state.turn_index == 7

    assert state.current_screen is not None
    assert state.current_screen.canonical_id == "screen:year"
    assert state.current_screen.safe_label == "Año"
    assert state.current_screen.route == "/admin/general/anios"

    assert state.current_module is None

    assert state.resolved_entities == (state.current_screen,)

    assert state.unresolved_entities == ()
    assert state.relevant_evidence_refs == ()


def test_unknown_current_route_clears_previous_screen_scope():
    import uuid
    from types import SimpleNamespace

    from erp_assistant.retrieval.conversation_context import (
        ConversationEntity,
        ConversationState,
    )

    class Session:
        def scalars(self, statement):
            return []

    version = SimpleNamespace(
        id=uuid.uuid4(),
        erp_id="erp:test",
        knowledge_version="v-active",
    )

    retriever = HybridKnowledgeRetriever(
        Session(),
        chroma=None,
        neo4j=None,
        embeddings=None,
    )

    previous = ConversationState(
        erp_id="erp:test",
        knowledge_version="v-active",
        current_screen=ConversationEntity(
            canonical_id="screen:old",
            entity_type="screen",
            safe_label="Pantalla anterior",
            route="/admin/old",
        ),
        turn_index=3,
    )

    state = retriever._conversation_state_for_current_route(
        previous,
        "/admin/does-not-exist",
        version=version,
    )

    assert state.current_screen is None
    assert state.current_module is None
    assert state.resolved_entities == ()
    assert state.unresolved_entities == ()
    assert state.turn_index == 3


def test_external_current_route_fails_closed_without_database_lookup():
    import uuid
    from types import SimpleNamespace

    from erp_assistant.retrieval.conversation_context import (
        ConversationEntity,
        ConversationState,
    )

    class Session:
        def scalars(self, statement):
            raise AssertionError("external route must not reach PostgreSQL screen lookup")

    version = SimpleNamespace(
        id=uuid.uuid4(),
        erp_id="erp:test",
        knowledge_version="v-active",
    )

    retriever = HybridKnowledgeRetriever(
        Session(),
        chroma=None,
        neo4j=None,
        embeddings=None,
    )

    previous = ConversationState(
        erp_id="erp:test",
        knowledge_version="v-active",
        current_screen=ConversationEntity(
            canonical_id="screen:old",
            entity_type="screen",
            safe_label="Pantalla anterior",
            route="/admin/old",
        ),
        turn_index=4,
    )

    state = retriever._conversation_state_for_current_route(
        previous,
        "https://example.invalid/admin/general/anios",
        version=version,
    )

    assert state.current_screen is None
    assert state.current_module is None
    assert state.turn_index == 4
