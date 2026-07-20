from __future__ import annotations

import argparse
import os

from src.vectorstore import ChromaRepository, OllamaEmbeddingClient

from .database_common import print_json, project_path


def build_parser():
    parser = argparse.ArgumentParser(description="Busca conocimiento semántico local")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--erp-id")
    parser.add_argument("--knowledge-version")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.top_k < 1:
            raise ValueError("top-k debe ser positivo")
        embeddings = OllamaEmbeddingClient()
        vector = embeddings.embed(args.query)[0]
        path = os.getenv("ERP_ASSISTANT_CHROMA_PATH") or project_path("data/vectorstore/chroma")
        results = ChromaRepository(path=path).query(
            vector,
            top_k=args.top_k,
            erp_id=args.erp_id,
            knowledge_version=args.knowledge_version,
        )
        print_json({"status": "ok", "query": args.query, "results": results}, pretty=args.pretty)
        return 0
    except Exception as exc:
        print_json({"status": "error", "error": str(exc)[:400]}, pretty=args.pretty)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
