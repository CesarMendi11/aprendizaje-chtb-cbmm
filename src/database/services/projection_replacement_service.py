from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus
from src.database.models import KnowledgeVersionPromotion, KnowledgeVersionRecord


class ProjectionReplacementError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectionReplacementLineage:
    knowledge_version_id: uuid.UUID
    knowledge_version: str
    erp_id: str
    previous_active_version_id: uuid.UUID | None
    previous_active_knowledge_version: str | None

    @property
    def replacement(self) -> bool:
        return self.previous_active_version_id is not None


class ProjectionReplacementService:
    """Resolve immutable promotion lineage for derived projection replacement."""

    def __init__(self, session: Session):
        self.session = session

    def resolve(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        require_active: bool = True,
    ) -> ProjectionReplacementLineage:
        version_id = self._uuid(knowledge_version_id)
        version = self.session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise ProjectionReplacementError("KnowledgeVersion no encontrada para proyección.")
        if require_active and version.status != KnowledgeVersionStatus.ACTIVE:
            raise ProjectionReplacementError(
                "La KnowledgeVersion objetivo ya no está ACTIVE para proyección."
            )

        promotion = self.session.scalar(
            select(KnowledgeVersionPromotion).where(
                KnowledgeVersionPromotion.knowledge_version_id == version.id
            )
        )
        if promotion is None or promotion.previous_active_version_id is None:
            return ProjectionReplacementLineage(
                knowledge_version_id=version.id,
                knowledge_version=version.knowledge_version,
                erp_id=version.erp_id,
                previous_active_version_id=None,
                previous_active_knowledge_version=None,
            )

        previous = self.session.get(
            KnowledgeVersionRecord,
            promotion.previous_active_version_id,
        )
        if previous is None:
            raise ProjectionReplacementError(
                "La ACTIVE anterior fijada por Promotion ya no existe."
            )
        if previous.erp_id != version.erp_id:
            raise ProjectionReplacementError(
                "Promotion intenta reemplazar una versión de otro ERP."
            )
        if previous.id == version.id:
            raise ProjectionReplacementError(
                "Promotion no puede reemplazar la misma KnowledgeVersion."
            )
        if previous.status != KnowledgeVersionStatus.ARCHIVED:
            raise ProjectionReplacementError(
                "La ACTIVE anterior fijada por Promotion no está ARCHIVED."
            )

        return ProjectionReplacementLineage(
            knowledge_version_id=version.id,
            knowledge_version=version.knowledge_version,
            erp_id=version.erp_id,
            previous_active_version_id=previous.id,
            previous_active_knowledge_version=previous.knowledge_version,
        )

    @staticmethod
    def _uuid(value: uuid.UUID | str) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ProjectionReplacementError("knowledge_version_id inválido.") from exc
