from src.graph.mapper import GraphMapper
from src.graph.repository import Neo4jRepository


class FakeClient:
    def __init__(self):
        self.calls = []

    def execute(self, query, parameters=None, *, write=False):
        self.calls.append((query, parameters or {}, write))
        return [{"count": 0}] if "RETURN count(n) AS count" in query else []


def _node(cid):
    return GraphMapper().map_node(
        entity_type="screen",
        payload={
            "id": cid,
            "title": "Synthetic",
            "normalized_title": "synthetic",
            "route": "/safe",
        },
        content_hash="hash",
        review_status="approved",
        erp_id="erp:test",
        knowledge_version="v1",
        projected_at="2026-01-01T00:00:00+00:00",
    )


def test_repository_uses_parameterized_merge_and_batches():
    client = FakeClient()
    repo = Neo4jRepository(client)
    assert repo.upsert_nodes([_node("screen:1"), _node("screen:2")], batch_size=1) == 2
    assert len(client.calls) == 2
    assert all(
        "$rows" in query and "screen:1" not in query and parameters["rows"]
        for query, parameters, _ in client.calls
    )


def test_replace_is_strictly_namespaced_and_parameterized():
    client = FakeClient()
    Neo4jRepository(client).replace_version("erp:test", "v1")
    query, parameters, write = client.calls[0]
    assert "MATCH (n:ERPAssistantEntity" in query and "DETACH DELETE n" in query
    assert "MATCH (n) DETACH DELETE n" not in query
    assert parameters == {
        "managed_by": "erp_assistant",
        "erp_id": "erp:test",
        "knowledge_version": "v1",
    }
    assert write is True


def test_relationship_merge_is_parameterized_and_batched():
    mapper = GraphMapper()
    relationship = mapper.map_relationship(
        "TARGETS", "link:one", "screen:one", erp_id="erp:test", knowledge_version="v1"
    )
    client = FakeClient()
    repo = Neo4jRepository(client)
    assert repo.upsert_relationships([relationship], batch_size=1) == 1
    query, parameters, write = client.calls[0]
    assert "$rows" in query and relationship.source_key not in query
    assert parameters["rows"][0]["relationship_key"] == relationship.key
    assert write is True


def test_inspection_has_screen_query_avoids_static_relationship_token():
    client = FakeClient()
    Neo4jRepository(client).inspect("erp:external", "version:external")
    query, parameters, write = next(
        call for call in client.calls if "screens ORDER BY module_id" in call[0]
    )
    assert "MATCH (m:Module)-[r]->(s:Screen)" in query
    assert "type(r) = 'HAS_SCREEN'" in query
    assert "[:HAS_SCREEN]" not in query
    assert "m.managed_by = $managed_by" in query
    assert "$erp_id" in query and "$knowledge_version" in query
    assert "erp:external" not in query and "version:external" not in query
    assert parameters == {
        "managed_by": "erp_assistant",
        "erp_id": "erp:external",
        "knowledge_version": "version:external",
    }
    assert write is False
