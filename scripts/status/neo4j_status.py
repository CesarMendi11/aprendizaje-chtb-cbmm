from __future__ import annotations

from erp_assistant.config.neo4j_settings import Neo4jSettings

from scripts.common.database import print_json
from scripts.common.neo4j import neo4j_client, safe_neo4j_error


def main():
    settings = Neo4jSettings()
    try:
        with neo4j_client(settings) as client:
            from erp_assistant.projections.neo4j.repository import Neo4jRepository

            print_json(
                {
                    "connectivity": "ok",
                    "uri": settings.safe_uri,
                    "database": settings.database,
                    "server": client.verify(),
                    **Neo4jRepository(client).status(),
                },
                pretty=True,
            )
        return 0
    except Exception as exc:
        print_json(
            {
                "connectivity": "error",
                "uri": settings.safe_uri,
                "database": settings.database,
                "error": safe_neo4j_error(exc, settings),
            },
            pretty=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
