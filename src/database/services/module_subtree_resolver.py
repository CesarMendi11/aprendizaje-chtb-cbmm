from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus
from src.database.models import KnowledgeItem, KnowledgeVersionRecord


class ModuleSubtreeResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ModuleCrawlSubtree:
    knowledge_version_id: uuid.UUID
    knowledge_version: str
    erp_id: str
    root_module_id: str
    root_module_name: str
    ancestor_module_ids: tuple[str, ...]
    module_ids: tuple[str, ...]
    known_screen_ids: tuple[str, ...]
    known_screen_routes: tuple[str, ...]
    unroutable_screen_ids: tuple[str, ...]
    navigation_path: tuple[str, ...]
    navigation_origin_path: tuple[str, ...]


class ModuleSubtreeResolver:
    """Resolve one ACTIVE canonical module into its deterministic crawl subtree.

    PostgreSQL is the authority. Module ancestry comes from
    ``KnowledgeItem.parent_canonical_id`` and screens belong to the most-specific
    module stored as their parent. No route-prefix heuristics are used.
    """

    def __init__(self, session: Session):
        self.session = session

    def resolve(
        self,
        target_module_id: str,
        *,
        knowledge_version_id: uuid.UUID | str | None = None,
    ) -> ModuleCrawlSubtree:
        module_id = str(target_module_id or "").strip()
        if not module_id.startswith("module:"):
            raise ModuleSubtreeResolutionError(
                "target_module_id debe ser un identificador canónico de módulo"
            )

        version, root = self._resolve_target(module_id, knowledge_version_id)
        items = list(
            self.session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == version.id,
                    KnowledgeItem.entity_type.in_(("module", "screen")),
                )
            )
        )
        modules = {item.canonical_id: item for item in items if item.entity_type == "module"}
        screens = [item for item in items if item.entity_type == "screen"]

        if root.canonical_id not in modules:
            raise ModuleSubtreeResolutionError(
                "El módulo objetivo no pertenece a la versión de conocimiento seleccionada"
            )

        ancestor_ids = self._ancestors(root, modules, version.erp_id)
        module_ids = self._descendants(root.canonical_id, modules)
        selected_modules = [modules[module_id] for module_id in module_ids]
        selected_modules.sort(key=self._module_sort_key)
        ordered_module_ids = tuple(item.canonical_id for item in selected_modules)
        selected_module_ids = set(ordered_module_ids)

        selected_screens = [
            item for item in screens if item.parent_canonical_id in selected_module_ids
        ]
        selected_screens.sort(key=self._screen_sort_key)

        routes: list[str] = []
        unroutable: list[str] = []
        seen_routes: set[str] = set()
        for screen in selected_screens:
            route = str(screen.route or "").strip()
            if not route or not route.startswith("/") or "://" in route:
                unroutable.append(screen.canonical_id)
                continue
            if route not in seen_routes:
                routes.append(route)
                seen_routes.add(route)

        payload = dict(root.source_payload or {})
        navigation_path = self._string_tuple(payload.get("navigation_path"))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        origin_value = metadata.get("navigation_origin_path") if metadata else None
        navigation_origin_path = self._origin_path(origin_value)

        return ModuleCrawlSubtree(
            knowledge_version_id=version.id,
            knowledge_version=version.knowledge_version,
            erp_id=version.erp_id,
            root_module_id=root.canonical_id,
            root_module_name=str(root.title or payload.get("name") or root.canonical_id),
            ancestor_module_ids=ancestor_ids,
            module_ids=ordered_module_ids,
            known_screen_ids=tuple(item.canonical_id for item in selected_screens),
            known_screen_routes=tuple(routes),
            unroutable_screen_ids=tuple(unroutable),
            navigation_path=navigation_path,
            navigation_origin_path=navigation_origin_path,
        )

    def _resolve_target(
        self,
        module_id: str,
        knowledge_version_id: uuid.UUID | str | None,
    ) -> tuple[KnowledgeVersionRecord, KnowledgeItem]:
        query = (
            select(KnowledgeVersionRecord, KnowledgeItem)
            .join(
                KnowledgeItem,
                KnowledgeItem.knowledge_version_id == KnowledgeVersionRecord.id,
            )
            .where(
                KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
                KnowledgeItem.entity_type == "module",
                KnowledgeItem.canonical_id == module_id,
            )
        )
        if knowledge_version_id is not None:
            try:
                version_id = uuid.UUID(str(knowledge_version_id))
            except (TypeError, ValueError) as exc:
                raise ModuleSubtreeResolutionError(
                    "knowledge_version_id no es un UUID válido"
                ) from exc
            query = query.where(KnowledgeVersionRecord.id == version_id)

        matches = list(self.session.execute(query).all())
        if not matches:
            if knowledge_version_id is None:
                raise ModuleSubtreeResolutionError(
                    "El módulo objetivo no existe en una versión ACTIVE"
                )
            raise ModuleSubtreeResolutionError(
                "El módulo objetivo no existe en la versión ACTIVE indicada"
            )
        if len(matches) > 1:
            raise ModuleSubtreeResolutionError(
                "El módulo objetivo es ambiguo entre múltiples versiones ACTIVE"
            )
        return matches[0][0], matches[0][1]

    @staticmethod
    def _ancestors(
        root: KnowledgeItem,
        modules: dict[str, KnowledgeItem],
        erp_id: str,
    ) -> tuple[str, ...]:
        ancestry: list[str] = []
        seen = {root.canonical_id}
        parent_id = root.parent_canonical_id

        while parent_id and parent_id != erp_id:
            if parent_id in seen:
                raise ModuleSubtreeResolutionError(
                    "La jerarquía de módulos contiene un ciclo en los ancestros"
                )
            parent = modules.get(parent_id)
            if parent is None:
                raise ModuleSubtreeResolutionError(
                    f"Ancestro de módulo no encontrado: {parent_id}"
                )
            ancestry.append(parent_id)
            seen.add(parent_id)
            parent_id = parent.parent_canonical_id

        if parent_id != erp_id:
            raise ModuleSubtreeResolutionError(
                "La jerarquía del módulo no termina en el ERP esperado"
            )

        ancestry.reverse()
        return tuple(ancestry)

    @staticmethod
    def _descendants(
        root_module_id: str,
        modules: dict[str, KnowledgeItem],
    ) -> set[str]:
        children: dict[str, list[str]] = {}
        for item in modules.values():
            if item.parent_canonical_id in modules:
                children.setdefault(str(item.parent_canonical_id), []).append(item.canonical_id)

        selected: set[str] = set()
        frontier = [root_module_id]
        while frontier:
            current = frontier.pop()
            if current in selected:
                raise ModuleSubtreeResolutionError(
                    "La jerarquía de módulos contiene un ciclo en el subárbol"
                )
            selected.add(current)
            frontier.extend(children.get(current, ()))
        return selected

    @staticmethod
    def _module_sort_key(item: KnowledgeItem) -> tuple[Any, ...]:
        payload = dict(item.source_payload or {})
        depth = payload.get("depth")
        try:
            normalized_depth = int(depth)
        except (TypeError, ValueError):
            normalized_depth = 0
        navigation_path = ModuleSubtreeResolver._string_tuple(payload.get("navigation_path"))
        return normalized_depth, tuple(value.casefold() for value in navigation_path), item.canonical_id

    @staticmethod
    def _screen_sort_key(item: KnowledgeItem) -> tuple[int, str, str]:
        route = str(item.route or "").strip()
        return (0 if route else 1), route, item.canonical_id

    @staticmethod
    def _string_tuple(value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(
            clean
            for item in value
            if (clean := str(item or "").strip())
        )

    @staticmethod
    def _origin_path(value: Any) -> tuple[str, ...]:
        if not isinstance(value, str):
            return ()
        return tuple(part.strip() for part in value.split("||") if part.strip())
