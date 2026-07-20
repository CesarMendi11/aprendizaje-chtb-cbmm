from __future__ import annotations

import argparse

from src.config.database_settings import DatabaseConfigurationError, DatabaseSettings
from src.database.services import CanonicalImportService
from src.database.session import session_scope

from .database_common import database_engine, print_json, project_path


def build_parser():
    parser = argparse.ArgumentParser(description="Importa conocimiento canónico a PostgreSQL")
    parser.add_argument("--knowledge", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--build-report")
    parser.add_argument("--activate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sync-jobs", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    paths = [project_path(args.knowledge), project_path(args.manifest)]
    report = project_path(args.build_report) if args.build_report else None
    try:
        if args.dry_run:
            result = CanonicalImportService.__new__(CanonicalImportService).dry_run(
                paths[0], paths[1], report
            )
        else:
            settings = DatabaseSettings()
            create_jobs = settings.create_sync_jobs if args.sync_jobs is None else args.sync_jobs
            with session_scope(database_engine()) as session:
                result = CanonicalImportService(session).import_canonical(
                    paths[0], paths[1], report,
                    activate=args.activate, create_sync_jobs=create_jobs
                )
        if args.strict and result.warnings:
            raise ValueError(f"Importación strict rechazada: {result.warnings} advertencias")
        print_json(result, pretty=args.pretty)
        return 0
    except (DatabaseConfigurationError, OSError, ValueError) as exc:
        print_json({"status": "error", "error": str(exc)[:500]}, pretty=args.pretty)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
