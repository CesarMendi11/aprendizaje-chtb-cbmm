from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from src.knowledge.canonical.ids import content_hash

from .mapper import GraphMapper
from .models import ProjectionPlan


class GraphProjectionService:
    def __init__(self, mapper: GraphMapper | None = None):
        self.mapper = mapper or GraphMapper()

    def build_plan(
        self,
        entries,
        *,
        erp_id: str,
        knowledge_version: str,
        sync_job_id: str | None = None,
        sync_job_status: str | None = None,
        sync_job_attempt_count: int | None = None,
        projected_at: str | None = None,
    ):
        stamp = projected_at or datetime.now(timezone.utc).isoformat()
        nodes = []
        payloads = []
        counts = Counter()
        for entry in entries:
            entity_type = entry["entity_type"]
            payload = entry["payload"]
            node = self.mapper.map_node(
                entity_type=entity_type,
                payload=payload,
                content_hash=entry["content_hash"],
                review_status=entry["review_status"],
                erp_id=erp_id,
                knowledge_version=knowledge_version,
                projected_at=stamp,
            )
            nodes.append(node)
            payloads.append((entity_type, payload))
            counts[entity_type] += 1
        node_ids = {node.properties["canonical_id"] for node in nodes}
        relationships = []
        skipped = Counter()
        for entity_type, payload in payloads:
            for rel_type, source_id, target_id in self.mapper.relationship_candidates(
                entity_type, payload
            ):
                if source_id not in node_ids or target_id not in node_ids:
                    skipped["endpoint_not_projected"] += 1
                    continue
                relationships.append(
                    self.mapper.map_relationship(
                        rel_type,
                        source_id,
                        target_id,
                        erp_id=erp_id,
                        knowledge_version=knowledge_version,
                    )
                )
        relationships = sorted(
            {rel.key: rel for rel in relationships}.values(), key=lambda rel: rel.key
        )
        nodes = sorted(nodes, key=lambda node: node.key)
        digest = content_hash(
            {
                "nodes": [
                    (node.key, node.properties["content_hash"], node.properties["review_status"])
                    for node in nodes
                ],
                "relationships": [rel.key for rel in relationships],
            }
        )
        return ProjectionPlan(
            erp_id,
            knowledge_version,
            len(nodes),
            dict(sorted(counts.items())),
            tuple(nodes),
            tuple(relationships),
            sum(skipped.values()),
            dict(sorted(skipped.items())),
            digest,
            sync_job_id,
            sync_job_status,
            sync_job_attempt_count,
        )
