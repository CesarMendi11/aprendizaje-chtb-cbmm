from __future__ import annotations

import argparse

from erp_assistant.config.neo4j_settings import Neo4jSettings
from erp_assistant.persistence.postgres.enums import SyncTarget
from erp_assistant.persistence.postgres.repositories import KnowledgeRepository, SyncJobRepository
from erp_assistant.persistence.postgres.session import session_scope
from erp_assistant.projections.neo4j.repository import Neo4jRepository

from scripts.common.database import database_engine, print_json
from scripts.common.neo4j import neo4j_client, safe_neo4j_error


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspecciona la proyección Neo4j administrada")
    parser.add_argument("--erp-id")
    parser.add_argument("--knowledge-version")
    args = parser.parse_args(argv)
    try:
        settings = Neo4jSettings()
        with session_scope(database_engine()) as session, neo4j_client(settings) as client:
            versions = KnowledgeRepository(session).list_versions(args.erp_id)
            if args.knowledge_version:
                versions = [v for v in versions if v.knowledge_version == args.knowledge_version]
            jobs = []
            for version in versions:
                job = SyncJobRepository(session).get(version.id, SyncTarget.NEO4J)
                if job:
                    jobs.append(
                        {
                            "knowledge_version": version.knowledge_version,
                            "status": str(job.status),
                            "attempt_count": job.attempt_count,
                            "checkpoint": job.checkpoint,
                        }
                    )
            report = Neo4jRepository(client).inspect(args.erp_id, args.knowledge_version)
            print_json(
                {
                    "erp_id": args.erp_id,
                    "knowledge_version": args.knowledge_version,
                    **report,
                    "sync_jobs": jobs,
                    "omitted_references": sum(
                        (job.get("checkpoint") or {}).get("skipped_relationships", 0)
                        for job in jobs
                    ),
                },
                pretty=True,
            )
        return 0
    except Exception as exc:
        settings = locals().get("settings", Neo4jSettings())
        print_json({"status": "error", "error": safe_neo4j_error(exc, settings)}, pretty=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
