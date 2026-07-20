from __future__ import annotations

from typing import Iterable

from .mapper import LABELS, MANAGED_BY
from .models import GraphNode, GraphRelationship
from .schema import MANAGED_SCHEMA

RELATIONSHIP_TYPES = {
    "HAS_MODULE",
    "HAS_SCREEN",
    "HAS_STATE",
    "HAS_FIELD",
    "HAS_CONTROL",
    "HAS_TABLE",
    "HAS_COLUMN",
    "HAS_LINK",
    "HAS_EVENT",
    "HAS_EVIDENCE",
    "TARGETS",
    "FROM_STATE",
    "TO_STATE",
    "TRIGGERED_BY",
}


class Neo4jRepository:
    def __init__(self, client):
        self.client = client

    def bootstrap(self):
        for query in MANAGED_SCHEMA:
            self.client.execute(query, {}, write=True)

    def upsert_nodes(self, nodes: Iterable[GraphNode], *, batch_size=200) -> int:
        total = 0
        grouped: dict[str, list[GraphNode]] = {}
        for node in nodes:
            if node.label not in set(LABELS.values()):
                raise ValueError("Etiqueta Neo4j no permitida")
            grouped.setdefault(node.label, []).append(node)
        for label, values in grouped.items():
            query = (
                f"UNWIND $rows AS row MERGE "
                f"(n:ERPAssistantEntity:{label} {{node_key: row.node_key}}) "
                "SET n += row.properties"
            )
            for batch in _batches(values, batch_size):
                rows = [{"node_key": node.key, "properties": node.properties} for node in batch]
                self.client.execute(query, {"rows": rows}, write=True)
                total += len(rows)
        return total

    def upsert_relationships(
        self, relationships: Iterable[GraphRelationship], *, batch_size=200
    ) -> int:
        total = 0
        grouped: dict[str, list[GraphRelationship]] = {}
        for relationship in relationships:
            if relationship.relationship_type not in RELATIONSHIP_TYPES:
                raise ValueError("Relación Neo4j no permitida")
            grouped.setdefault(relationship.relationship_type, []).append(relationship)
        for rel_type, values in grouped.items():
            query = (
                "UNWIND $rows AS row MATCH (s:ERPAssistantEntity {node_key: row.source_key}) "
                "MATCH (t:ERPAssistantEntity {node_key: row.target_key}) "
                f"MERGE (s)-[r:{rel_type} {{relationship_key: row.relationship_key}}]->(t) "
                "SET r += row.properties"
            )
            for batch in _batches(values, batch_size):
                rows = [
                    {
                        "source_key": rel.source_key,
                        "target_key": rel.target_key,
                        "relationship_key": rel.key,
                        "properties": rel.properties,
                    }
                    for rel in batch
                ]
                self.client.execute(query, {"rows": rows}, write=True)
                total += len(rows)
        return total

    def replace_version(self, erp_id: str, knowledge_version: str):
        query = (
            "MATCH (n:ERPAssistantEntity {managed_by: $managed_by, erp_id: $erp_id, "
            "knowledge_version: $knowledge_version}) DETACH DELETE n"
        )
        self.client.execute(
            query,
            {"managed_by": MANAGED_BY, "erp_id": erp_id, "knowledge_version": knowledge_version},
            write=True,
        )

    def status(self):
        constraints = self.client.execute(
            "SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH $prefix RETURN count(*) AS count",
            {"prefix": "erp_assistant_"},
        )
        counts = self.client.execute(
            "MATCH (n:ERPAssistantEntity {managed_by: $managed_by}) "
            "OPTIONAL MATCH (n)-[r {managed_by: $managed_by}]->() "
            "RETURN count(DISTINCT n) AS nodes, count(DISTINCT r) AS relationships, "
            "collect(DISTINCT n.knowledge_version) AS versions",
            {"managed_by": MANAGED_BY},
        )
        return {
            "constraints": constraints[0]["count"] if constraints else 0,
            **(counts[0] if counts else {"nodes": 0, "relationships": 0, "versions": []}),
        }

    def inspect(self, erp_id: str | None = None, knowledge_version: str | None = None):
        params = {
            "managed_by": MANAGED_BY,
            "erp_id": erp_id,
            "knowledge_version": knowledge_version,
        }
        where = (
            "n.managed_by = $managed_by "
            "AND ($erp_id IS NULL OR n.erp_id = $erp_id) "
            "AND ($knowledge_version IS NULL OR n.knowledge_version = $knowledge_version)"
        )
        return {
            "nodes_by_label": self.client.execute(
                f"MATCH (n:ERPAssistantEntity) WHERE {where} UNWIND labels(n) AS label "
                "WITH label WHERE label <> 'ERPAssistantEntity' "
                "RETURN label, count(*) AS count ORDER BY label",
                params,
            ),
            "relationships_by_type": self.client.execute(
                f"MATCH (n:ERPAssistantEntity)-[r]->() WHERE {where} "
                "RETURN type(r) AS type, count(*) AS count ORDER BY type",
                params,
            ),
            "screens_by_module": self.client.execute(
                "MATCH (m:Module)-[r]->(s:Screen) WHERE type(r) = 'HAS_SCREEN' "
                "AND m.managed_by = $managed_by "
                "AND ($erp_id IS NULL OR m.erp_id = $erp_id) "
                "AND ($knowledge_version IS NULL OR m.knowledge_version = $knowledge_version) "
                "RETURN m.canonical_id AS module_id, count(s) AS screens ORDER BY module_id",
                params,
            ),
            "unconnected_nodes": self.client.execute(
                f"MATCH (n:ERPAssistantEntity) WHERE {where} "
                "AND NOT (n)--() RETURN count(n) AS count",
                params,
            )[0]["count"],
        }


def _batches(values, size):
    if size < 1:
        raise ValueError("batch_size debe ser positivo")
    for index in range(0, len(values), size):
        yield values[index : index + size]
