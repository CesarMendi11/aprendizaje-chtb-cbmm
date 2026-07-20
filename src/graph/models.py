from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphNode:
    key: str
    label: str
    properties: dict[str, Any]


@dataclass(frozen=True)
class GraphRelationship:
    key: str
    relationship_type: str
    source_key: str
    target_key: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProjectionPlan:
    erp_id: str
    knowledge_version: str
    eligible_items: int
    items_by_type: dict[str, int]
    nodes: tuple[GraphNode, ...]
    relationships: tuple[GraphRelationship, ...]
    skipped_relationships: int
    skipped_reasons: dict[str, int]
    projection_hash: str
    sync_job_id: str | None = None
    sync_job_status: str | None = None
    sync_job_attempt_count: int | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "erp_id": self.erp_id,
            "knowledge_version": self.knowledge_version,
            "eligible_items": self.eligible_items,
            "items_by_type": self.items_by_type,
            "nodes": len(self.nodes),
            "relationships": len(self.relationships),
            "skipped_relationships": self.skipped_relationships,
            "skipped_reasons": self.skipped_reasons,
            "projection_hash": self.projection_hash,
            "sync_job": {
                "id": self.sync_job_id,
                "status": self.sync_job_status,
                "attempt_count": self.sync_job_attempt_count,
            }
            if self.sync_job_id
            else None,
        }
