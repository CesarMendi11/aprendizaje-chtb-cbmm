from __future__ import annotations

import argparse
import os

from src.config.neo4j_settings import Neo4jSettings
from src.database.session import session_scope
from src.graph.client import Neo4jClient
from src.hybrid import HybridKnowledgeRetriever
from src.hybrid.aliases import semantic_aliases_for
from src.vectorstore import ChromaRepository, OllamaEmbeddingClient
from src.vectorstore.ollama_generation import OllamaGenerationClient

from .database_common import database_engine, print_json, project_path


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
            chroma = ChromaRepository(
                path=os.getenv("ERP_ASSISTANT_CHROMA_PATH")
                or project_path("data/vectorstore/chroma")
            )
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
