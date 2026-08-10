from src.hybrid.retriever import ABSTAIN, ALLOWED_RELATIONSHIPS, HybridKnowledgeRetriever


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
    retriever._expand(["field:1"], "erp:s", "v1", 5)
    assert "[*1..2]-(b)" in graph.query
    assert "relationships(p)" in graph.query
    assert "WRITE" not in graph.query.upper()
    assert set(graph.parameters["rels"]) == ALLOWED_RELATIONSHIPS
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


def test_retrieve_uses_only_reauthorized_semantic_screen_as_graph_seed(monkeypatch):
    from types import SimpleNamespace

    version = SimpleNamespace(
        id="version-db-id",
        erp_id="erp:test",
        knowledge_version="v1",
    )

    class SyncService:
        def __init__(self, session):
            pass

        def prepare(self, *, erp_id=None, knowledge_version=None):
            return version, [], {}

    monkeypatch.setattr("src.hybrid.retriever.ChromaSyncService", SyncService)

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
    assert graph.parameters["seeds"] == ["screen:retenciones"]
    assert result["retrieval"]["semantic_candidates"] == 1
    assert result["retrieval"]["approved_semantic_hits"] == 1
    assert result["sources"][0]["origin"] == "approved_semantic"
    assert result["approved_semantics"][0]["semantic_id"] == "semantic:retenciones-purpose"
