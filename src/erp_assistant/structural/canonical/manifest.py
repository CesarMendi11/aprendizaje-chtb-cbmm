from __future__ import annotations

from typing import Any

from .ids import content_hash
from .models import CanonicalKnowledgeBase
from .snapshot import CanonicalSnapshotContext


def create_manifest(
    knowledge: CanonicalKnowledgeBase,
    knowledge_payload: dict[str, Any],
    *,
    snapshot_context: CanonicalSnapshotContext | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot_context or CanonicalSnapshotContext.full()
    manifest = {
        "schema_version": knowledge.schema_version,
        "knowledge_version": knowledge.knowledge_version,
        "generated_at": knowledge.generated_at.isoformat(),
        "erp": {"id": knowledge.erp_system.id, "slug": knowledge.erp_system.slug, "name": knowledge.erp_system.name},
        "profile": knowledge.source_profile,
        "source_artifacts": knowledge.source_artifacts,
        "source_artifact_hashes": knowledge.source_artifact_hashes,
        "canonical_document_hash": content_hash(knowledge_payload),
        "snapshot": snapshot.model_dump(mode="json"),
        "entity_counts": knowledge.statistics,
    }
    if extra_metadata:
        collisions = sorted(set(manifest) & set(extra_metadata))
        if collisions:
            raise ValueError(
                f"Metadata extra del manifest colisiona con campo reservado: {collisions[0]}"
            )
        manifest.update(extra_metadata)
    return manifest
