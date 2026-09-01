from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


class ModuleCrawlBoundaryError(ValueError):
    """Raised when a pinned MODULE crawl boundary is incomplete or inconsistent."""


@dataclass(frozen=True)
class ModuleNavigationStep:
    """One deterministic menu expansion required to enter a selected module branch."""

    depth: int
    label: str
    selector: str


@dataclass(frozen=True)
class ModuleCrawlBoundary:
    """Deterministic trust boundary for one MODULE crawl.

    The boundary does not infer module ownership from URL prefixes. It is built
    from the pinned PostgreSQL subtree supplied by ``PipelineJobRunner``.

    Known screen routes are always admissible seeds. A previously unknown route
    may only expand the crawl when it is newly revealed by an ``expand_menu``
    event whose menu path is already inside the selected module branch. This
    allows safe discovery of new descendant screens while preventing arbitrary
    links or sibling navigation from widening the scope.
    """

    root_module_id: str
    module_ids: tuple[str, ...]
    known_screen_routes: tuple[str, ...]
    navigation_path: tuple[str, ...]
    navigation_origin_path: tuple[str, ...]

    @classmethod
    def from_payload(cls, value: Any) -> "ModuleCrawlBoundary":
        if not isinstance(value, dict):
            raise ModuleCrawlBoundaryError("module_scope debe ser un objeto")

        root_module_id = str(value.get("root_module_id") or "").strip()
        if not root_module_id.startswith("module:"):
            raise ModuleCrawlBoundaryError(
                "module_scope.root_module_id debe ser un identificador canónico de módulo"
            )

        module_ids = cls._clean_tuple(value.get("module_ids"))
        if root_module_id not in module_ids:
            raise ModuleCrawlBoundaryError(
                "module_scope.module_ids no contiene el módulo raíz seleccionado"
            )
        if any(not item.startswith("module:") for item in module_ids):
            raise ModuleCrawlBoundaryError(
                "module_scope.module_ids contiene identificadores no canónicos"
            )

        navigation_path = cls._clean_tuple(value.get("navigation_path"))
        navigation_origin_path = cls._clean_tuple(value.get("navigation_origin_path"))
        if not navigation_path or not navigation_origin_path:
            raise ModuleCrawlBoundaryError(
                "MODULE requiere navigation_path y navigation_origin_path reproducibles"
            )
        if len(navigation_path) != len(navigation_origin_path):
            raise ModuleCrawlBoundaryError(
                "navigation_path y navigation_origin_path deben tener la misma profundidad"
            )

        raw_routes = cls._clean_tuple(value.get("known_screen_routes"))
        normalized_routes: list[str] = []
        seen_routes: set[str] = set()
        for route in raw_routes:
            normalized = cls.route_identity(route)
            if not normalized.startswith("/") or "://" in normalized:
                raise ModuleCrawlBoundaryError(f"Ruta conocida inválida en module_scope: {route}")
            if normalized not in seen_routes:
                seen_routes.add(normalized)
                normalized_routes.append(normalized)

        return cls(
            root_module_id=root_module_id,
            module_ids=module_ids,
            known_screen_routes=tuple(normalized_routes),
            navigation_path=navigation_path,
            navigation_origin_path=navigation_origin_path,
        )

    @property
    def entry_steps(self) -> tuple[ModuleNavigationStep, ...]:
        return tuple(
            ModuleNavigationStep(depth=index, label=label, selector=selector)
            for index, (label, selector) in enumerate(
                zip(self.navigation_path, self.navigation_origin_path, strict=True),
                start=1,
            )
        )

    def is_known_route(self, route: str | None) -> bool:
        if not route:
            return False
        return self.route_identity(route) in set(self.known_screen_routes)

    def is_inside_selected_branch(self, menu_selectors: Iterable[str]) -> bool:
        """Return True when a state path starts with the pinned module entry path."""
        observed = tuple(clean for item in menu_selectors if (clean := str(item or "").strip()))
        expected = self.navigation_origin_path
        return len(observed) >= len(expected) and observed[: len(expected)] == expected

    def allows_discovered_route(
        self,
        route: str | None,
        *,
        menu_selectors: Iterable[str] = (),
        event_category: str | None = None,
        newly_revealed: bool = False,
    ) -> bool:
        """Decide whether a discovered route may enter the MODULE frontier.

        Existing routes from the pinned subtree remain allowed independently of
        how they were observed. Unknown routes require positive structural
        provenance: they must be newly revealed by an ``expand_menu`` event
        reached through the selected module branch.
        """
        if not route:
            return False
        normalized = self.route_identity(route)
        if normalized in set(self.known_screen_routes):
            return True
        return bool(
            newly_revealed
            and str(event_category or "").strip() == "expand_menu"
            and self.is_inside_selected_branch(menu_selectors)
        )

    @staticmethod
    def route_identity(route: str) -> str:
        value = str(route or "").split("#", 1)[0].split("?", 1)[0].strip()
        if len(value) > 1:
            value = value.rstrip("/")
        return value or "/"

    @staticmethod
    def _clean_tuple(value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            return ()
        result: list[str] = []
        for item in value:
            clean = str(item or "").strip()
            if clean:
                result.append(clean)
        return tuple(result)
