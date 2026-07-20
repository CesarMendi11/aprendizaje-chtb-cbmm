from __future__ import annotations

import os
import uuid

import pytest

from src.config.neo4j_settings import Neo4jSettings
from src.graph.client import Neo4jClient
from src.graph.mapper import GraphMapper
from src.graph.repository import Neo4jRepository

pytestmark = pytest.mark.neo4j


def _settings():
    names = ("URI", "USER", "PASSWORD", "DATABASE")
    values = {name: os.getenv(f"TEST_NEO4J_{name}") for name in names}
    if not all(values.values()):
        pytest.skip("Neo4j de integración no configurado")
    return Neo4jSettings(
        uri=values["URI"],
        user=values["USER"],
        password=values["PASSWORD"],
        database=values["DATABASE"],
    )


def test_real_bootstrap_upsert_is_idempotent_and_cleanup_is_namespaced():
    settings = _settings()
    suffix = uuid.uuid4().hex
    erp_id, version = f"erp:test:{suffix}", f"version:{suffix}"
    other_erp_id, other_version = f"erp:other:{suffix}", f"other-version:{suffix}"
    node = GraphMapper().map_node(
        entity_type="erp_system",
        payload={
            "id": erp_id,
            "slug": "synthetic-test",
            "name": "Synthetic ERP",
            "profile_name": "test",
        },
        content_hash="a" * 64,
        review_status="approved",
        erp_id=erp_id,
        knowledge_version=version,
        projected_at="2026-01-01T00:00:00+00:00",
    )
    other_node = GraphMapper().map_node(
        entity_type="erp_system",
        payload={
            "id": other_erp_id,
            "slug": "synthetic-other",
            "name": "Other Synthetic ERP",
            "profile_name": "test",
        },
        content_hash="b" * 64,
        review_status="approved",
        erp_id=other_erp_id,
        knowledge_version=other_version,
        projected_at="2026-01-01T00:00:00+00:00",
    )
    with Neo4jClient(settings) as client:
        repo = Neo4jRepository(client)
        try:
            repo.bootstrap()
            repo.upsert_nodes([other_node])
            repo.upsert_nodes([node])
            repo.upsert_nodes([node])
            rows = client.execute(
                "MATCH (n:ERPAssistantEntity {node_key: $key}) RETURN count(n) AS count",
                {"key": node.key},
            )
            assert rows[0]["count"] == 1
            repo.replace_version(erp_id, version)
            rows = client.execute(
                "MATCH (n:ERPAssistantEntity {node_key: $key}) RETURN count(n) AS count",
                {"key": node.key},
            )
            assert rows[0]["count"] == 0
            rows = client.execute(
                "MATCH (n:ERPAssistantEntity {node_key: $key}) RETURN count(n) AS count",
                {"key": other_node.key},
            )
            assert rows[0]["count"] == 1
        finally:
            repo.replace_version(erp_id, version)
            repo.replace_version(other_erp_id, other_version)
