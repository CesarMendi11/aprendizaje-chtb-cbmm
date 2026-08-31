from __future__ import annotations

import argparse

from erp_assistant.config.chroma_settings import ChromaSettings
from erp_assistant.config.neo4j_settings import Neo4jSettings
from erp_assistant.persistence.postgres.session import session_scope
from erp_assistant.projections.neo4j.client import Neo4jClient
from erp_assistant.retrieval import HybridKnowledgeRetriever
from erp_assistant.retrieval.aliases import semantic_aliases_for
from erp_assistant.projections.chroma.structural_repository import ChromaRepository
from erp_assistant.integrations.ollama.embeddings import OllamaEmbeddingClient
from erp_assistant.integrations.ollama.generation import OllamaGenerationClient

from scripts.common.database import database_engine, print_json


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--question", required=True)
    p.add_argument("--erp-id")
    p.add_argument("--knowledge-version")
    p.add_argument("--semantic-top-k", type=int, default=8)
    p.add_argument("--graph-limit", type=int, default=20)
    p.add_argument("--no-generate", action="store_true")
    p.add_argument("--pretty", action="store_true")
    a = p.parse_args(argv)
    try:
        with session_scope(database_engine()) as session, Neo4jClient(Neo4jSettings()) as graph:
            chroma = ChromaRepository(path=ChromaSettings().path)
            generator = None if a.no_generate else OllamaGenerationClient()
            result = HybridKnowledgeRetriever(
                session,
                chroma=chroma,
                neo4j=graph,
                embeddings=OllamaEmbeddingClient(),
                generator=generator,
                aliases=semantic_aliases_for(a.erp_id),
            ).ask(
                a.question,
                generate=not a.no_generate,
                erp_id=a.erp_id,
                knowledge_version=a.knowledge_version,
                semantic_top_k=a.semantic_top_k,
                graph_limit=a.graph_limit,
            )
            if a.no_generate:
                result["context_preview"] = result.pop("context", "")[:2000]
            else:
                result.pop("context", None)
            print_json(result, pretty=a.pretty)
            return 0
    except Exception as exc:
        print_json({"status": "error", "error": str(exc)[:400]}, pretty=a.pretty)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
