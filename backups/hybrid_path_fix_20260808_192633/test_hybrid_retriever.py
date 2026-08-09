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
