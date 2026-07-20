from __future__ import annotations

import argparse

from src.config.neo4j_settings import Neo4jSettings
from src.database.services import Neo4jSyncService
from src.database.session import session_scope
from src.graph.repository import Neo4jRepository

from .database_common import database_engine, print_json
from .neo4j_common import neo4j_client, safe_neo4j_error


def build_parser():
    parser = argparse.ArgumentParser(description="Proyecta conocimiento aprobado a Neo4j")
    parser.add_argument("--erp-id")
    parser.add_argument("--knowledge-version")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--replace-version", action="store_true")
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    client = None
    settings = Neo4jSettings()
    try:
        if args.batch_size < 1:
            raise ValueError("batch-size debe ser positivo")
        with session_scope(database_engine()) as session:
            if args.dry_run:
                plan = Neo4jSyncService(session).prepare(
                    erp_id=args.erp_id, knowledge_version=args.knowledge_version
                )
                print_json({"status": "dry_run", **plan.summary()}, pretty=args.pretty)
                return 0
            if args.replace_version and not args.yes:
                answer = input("¿Reemplazar solo esta versión administrada? [y/N] ")
                if answer.strip().casefold() not in {"y", "yes", "s", "si", "sí"}:
                    print_json({"status": "cancelled"}, pretty=args.pretty)
                    return 1
            client = neo4j_client(settings)
            result = Neo4jSyncService(session, repository=Neo4jRepository(client)).run(
                erp_id=args.erp_id,
                knowledge_version=args.knowledge_version,
                batch_size=args.batch_size,
                replace_version=args.replace_version,
                allow_empty=args.allow_empty,
            )
            print_json({"status": result.status, **result.summary}, pretty=args.pretty)
            return 0 if result.status == "succeeded" else 2
    except Exception as exc:
        print_json(
            {"status": "error", "error": safe_neo4j_error(exc, settings)}, pretty=args.pretty
        )
        return 2
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
