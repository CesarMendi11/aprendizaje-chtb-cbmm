from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus
from erp_assistant.persistence.postgres.models import KnowledgeItem, KnowledgeVersionRecord
from erp_assistant.persistence.postgres.repositories import ReviewRepository


class ScreenScopeResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ScreenCrawlTarget:
    knowledge_version_id: uuid.UUID
    knowledge_version: str
    erp_id: str
    screen_id: str
    screen_title: str
    route: str
    module_id: str | None


class ScreenScopeResolver:
    """Resolve one exact screen route from the authoritative ACTIVE version."""

    def __init__(self, session: Session):
        self.session = session

    def resolve(
        self,
        route: str,
        *,
        knowledge_version_id: uuid.UUID | str | None = None,
    ) -> ScreenCrawlTarget:
        clean_route = str(route or "").strip()
        if not clean_route or not clean_route.startswith("/") or "://" in clean_route:
            raise ScreenScopeResolutionError("route debe ser una ruta interna canónica")

        query = (
            select(KnowledgeVersionRecord, KnowledgeItem)
            .join(
                KnowledgeItem,
                KnowledgeItem.knowledge_version_id == KnowledgeVersionRecord.id,
            )
            .where(
                KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.route == clean_route,
            )
        )
        if knowledge_version_id is not None:
            try:
                version_id = uuid.UUID(str(knowledge_version_id))
            except (TypeError, ValueError) as exc:
                raise ScreenScopeResolutionError(
                    "knowledge_version_id no es un UUID válido"
                ) from exc
            query = query.where(KnowledgeVersionRecord.id == version_id)

        matches = list(self.session.execute(query).all())
        if not matches:
            if knowledge_version_id is None:
                raise ScreenScopeResolutionError(
                    "La pantalla objetivo no existe en una versión ACTIVE"
                )
            raise ScreenScopeResolutionError(
                "La pantalla objetivo no existe en la versión ACTIVE indicada"
            )
        if len(matches) > 1:
            raise ScreenScopeResolutionError(
                "La pantalla objetivo es ambigua entre múltiples versiones ACTIVE"
            )

        version, screen = matches[0]
        module_id = str(screen.parent_canonical_id or "").strip() or None
        correction = ReviewRepository(self.session).latest_correction(screen.id)
        effective_payload = dict(
            correction.corrected_payload
            if correction is not None and correction.corrected_payload is not None
            else screen.source_payload or {}
        )
        screen_title = str(
            effective_payload.get("title") or screen.title or ""
        ).strip()
        if not screen_title:
            raise ScreenScopeResolutionError(
                "La pantalla objetivo no conserva un título canónico utilizable"
            )
        return ScreenCrawlTarget(
            knowledge_version_id=version.id,
            knowledge_version=version.knowledge_version,
            erp_id=version.erp_id,
            screen_id=screen.canonical_id,
            screen_title=screen_title,
            route=clean_route,
            module_id=module_id,
        )
