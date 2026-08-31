from __future__ import annotations

import argparse

from erp_assistant.projections.neo4j.subset_planner import Neo4jSubsetPlanner
from erp_assistant.projections.neo4j.subset_planner import SCOPES
from erp_assistant.persistence.postgres.session import session_scope
from erp_assistant.structural.canonical.privacy import sanitize_text

from scripts.common.database import database_engine, print_json


def build_parser():
    parser = argparse.ArgumentParser(
        description="Planifica un subconjunto PostgreSQL seguro para Neo4j"
    )
    parser.add_argument("--screen-route", required=True)
    parser.add_argument("--scope", choices=SCOPES, default="core")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        with session_scope(database_engine()) as session:
            report = Neo4jSubsetPlanner(session).plan(args.screen_route, scope=args.scope)
            print_json(report, pretty=args.pretty)
        return 0
    except Exception as exc:
        message, _ = sanitize_text(str(exc), 400)
        print_json({"status": "error", "error": message or "Error sanitizado"}, pretty=args.pretty)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
