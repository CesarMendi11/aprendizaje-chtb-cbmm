from __future__ import annotations

from typing import Any

from src.config.neo4j_settings import Neo4jSettings


class Neo4jClient:
    def __init__(self, settings: Neo4jSettings):
        settings.require()
        from neo4j import GraphDatabase

        self.settings = settings
        self.driver = GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))

    def execute(self, query: str, parameters: dict[str, Any] | None = None, *, write=False):
        with self.driver.session(database=self.settings.database) as session:
            fn = session.execute_write if write else session.execute_read
            return fn(lambda tx: [record.data() for record in tx.run(query, parameters or {})])

    def verify(self) -> dict[str, Any]:
        self.driver.verify_connectivity()
        info = self.driver.get_server_info()
        return {
            "address": str(info.address),
            "agent": info.agent,
            "protocol_version": str(info.protocol_version),
        }

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
