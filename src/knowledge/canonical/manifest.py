from __future__ import annotations

from typing import Any

from .ids import content_hash
from .models import CanonicalKnowledgeBase


def create_manifest(knowledge: CanonicalKnowledgeBase, knowledge_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": knowledge.schema_version,
        "knowledge_version": knowledge.knowledge_version,
        "generated_at": knowledge.generated_at.isoformat(),
        "erp": {"id": knowledge.erp_system.id, "slug": knowledge.erp_system.slug, "name": knowledge.erp_system.name},
        "profile": knowledge.source_profile,
        "source_artifacts": knowledge.source_artifacts,
        "source_artifact_hashes": knowledge.source_artifact_hashes,
        "canonical_document_hash": content_hash(knowledge_payload),
        "entity_counts": knowledge.statistics,
    }
