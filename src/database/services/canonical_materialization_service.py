from __future__ import annotations

import copy
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus
from src.database.models import KnowledgeItem, KnowledgeVersionRecord
from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.validator import CanonicalKnowledgeValidator


class CanonicalKnowledgeMaterializationError(ValueError):
    """Raised when PostgreSQL cannot reconstruct a valid full canonical snapshot."""


ENTITY_COLLECTIONS = {
    "module": "modules",
    "screen": "screens",
    "ui_state": "ui_states",
    "field": "fields",
    "control": "controls",
    "table": "tables",
    "table_column": "table_columns",
    "link": "links",
    "event": "events",
    "transition": "transitions",
    "evidence": "evidence",
}


class CanonicalKnowledgeMaterializer:
    """Reconstruct generated canonical knowledge from PostgreSQL authority.

    Structural review state is deliberately *not* folded into the generated payload.
    ``KnowledgeItem.source_payload`` remains the generated canonical source, while
    corrections and approvals stay in PostgreSQL review history. This is required so
    a later import can carry forward reviews by comparing the same generated content
    hashes instead of turning review decisions into apparent crawler changes.
    """

    GENERATOR_VERSION = "postgres-canonical-materializer-1.0.0"

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _source_profile(source_hashes: dict[str, object]) -> str | None:
        direct = [
            key.removeprefix("profile:")
            for key in sorted(source_hashes)
            if key.startswith("profile:") and key.removeprefix("profile:").strip()
        ]
        if direct:
            return direct[0]

        for key in sorted(source_hashes):
            candidate = key
            while candidate.startswith("base:"):
                candidate = candidate.removeprefix("base:")
            if candidate.startswith("profile:"):
                value = candidate.removeprefix("profile:").strip()
                if value:
                    return value
        return None

    def materialize(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        require_active: bool = False,
    ) -> CanonicalKnowledgeBase:
        try:
            version_id = uuid.UUID(str(knowledge_version_id))
        except (TypeError, ValueError) as exc:
            raise CanonicalKnowledgeMaterializationError(
                "knowledge_version_id inválido"
            ) from exc

        version = self.session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise CanonicalKnowledgeMaterializationError(
                "KnowledgeVersion base no encontrada"
            )
        if require_active and version.status != KnowledgeVersionStatus.ACTIVE:
            raise CanonicalKnowledgeMaterializationError(
                "La KnowledgeVersion base fijada ya no está ACTIVE"
            )

        items = list(
            self.session.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.knowledge_version_id == version.id)
                .order_by(KnowledgeItem.entity_type, KnowledgeItem.canonical_id)
            )
        )
        if not items:
            raise CanonicalKnowledgeMaterializationError(
                "La KnowledgeVersion base no contiene KnowledgeItems"
            )

        erp_items = [item for item in items if item.entity_type == "erp_system"]
        if len(erp_items) != 1:
            raise CanonicalKnowledgeMaterializationError(
                "La KnowledgeVersion base debe contener exactamente un erp_system"
            )
        erp_item = erp_items[0]
        if erp_item.canonical_id != version.erp_id:
            raise CanonicalKnowledgeMaterializationError(
                "El erp_system materializado no coincide con la KnowledgeVersion"
            )

        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in items:
            if item.entity_type == "erp_system":
                continue
            collection = ENTITY_COLLECTIONS.get(item.entity_type)
            if collection is None:
                raise CanonicalKnowledgeMaterializationError(
                    f"Tipo canónico no materializable: {item.entity_type}"
                )
            payload = copy.deepcopy(item.source_payload)
            if payload.get("id") != item.canonical_id:
                raise CanonicalKnowledgeMaterializationError(
                    f"source_payload inconsistente para {item.canonical_id}"
                )
            grouped[collection].append(payload)

        collections = {
            name: sorted(grouped.get(name, []), key=lambda item: str(item.get("id", "")))
            for name in ENTITY_COLLECTIONS.values()
        }
        statistics = {name: len(values) for name, values in collections.items()}
        source_hashes = dict(version.source_artifact_hashes or {})
        erp_payload = copy.deepcopy(erp_item.source_payload)
        source_profile = self._source_profile(source_hashes) or str(
            erp_payload.get("profile_name")
            or getattr(version.erp, "profile_name", "")
        )

        try:
            knowledge = CanonicalKnowledgeBase.model_validate(
                {
                    "schema_version": version.schema_version,
                    "knowledge_version": version.knowledge_version,
                    "generated_at": version.generated_at,
                    "generator_version": self.GENERATOR_VERSION,
                    "source_profile": source_profile,
                    "source_artifacts": sorted(source_hashes),
                    "source_artifact_hashes": source_hashes,
                    "erp_system": erp_payload,
                    "build_warnings": copy.deepcopy(version.build_warnings or []),
                    "statistics": statistics,
                    **collections,
                }
            )
        except Exception as exc:
            raise CanonicalKnowledgeMaterializationError(
                "PostgreSQL no pudo reconstruir un CanonicalKnowledgeBase válido"
            ) from exc

        errors = CanonicalKnowledgeValidator().errors(knowledge)
        if errors:
            codes = ", ".join(sorted({item.code for item in errors}))
            raise CanonicalKnowledgeMaterializationError(
                f"El canonical materializado desde PostgreSQL es inválido: {codes}"
            )
        return knowledge
