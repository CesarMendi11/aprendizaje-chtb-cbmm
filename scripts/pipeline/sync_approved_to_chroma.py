from __future__ import annotations

import argparse

from erp_assistant.config.chroma_settings import ChromaSettings
from erp_assistant.projections.chroma.structural_sync_service import ChromaSyncService
from erp_assistant.persistence.postgres.session import session_scope
from erp_assistant.projections.chroma.structural_repository import ChromaRepository
from erp_assistant.integrations.ollama.embeddings import OllamaEmbeddingClient

from scripts.common.database import database_engine, print_json


def build_parser():
    parser = argparse.ArgumentParser(description="Indexa conocimiento aprobado en ChromaDB")
    parser.add_argument("--erp-id")
    parser.add_argument("--knowledge-version")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        with session_scope(database_engine()) as session:
            if args.dry_run:
                _, _, summary = ChromaSyncService(session).prepare(
                    erp_id=args.erp_id, knowledge_version=args.knowledge_version
                )
                print_json({"status": "dry_run", **summary}, pretty=args.pretty)
                return 0
            repository = ChromaRepository(path=ChromaSettings().path)
            result = ChromaSyncService(
                session, repository=repository, embeddings=OllamaEmbeddingClient()
            ).run(erp_id=args.erp_id, knowledge_version=args.knowledge_version)
            print_json({"status": result.status, **result.summary}, pretty=args.pretty)
            return 0 if result.status == "succeeded" else 2
    except Exception as exc:
        print_json({"status": "error", "error": str(exc)[:400]}, pretty=args.pretty)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
