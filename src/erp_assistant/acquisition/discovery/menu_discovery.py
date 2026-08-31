from __future__ import annotations

import re
from typing import Any


class MenuDiscovery:
    """
    Descubre candidatos de menú en interfaces ERP modernas.

    Especialmente útil para:
    - Angular
    - Fuse Navigation
    - menús colapsables
    - sidebars
    - elementos sin href

    Este componente NO navega.
    Este componente NO hace clic.
    Solo identifica qué elementos parecen módulos o menús.
    """

    COLLAPSABLE_TAGS = {
        "fuse-vertical-navigation-collapsable-item",
        "mat-expansion-panel",
    }

    BASIC_TAGS = {
        "fuse-vertical-navigation-basic-item",
    }

    IGNORED_TEXTS = {
        "",
        "menu de navegación",
        "menu de navegacion",
    }

    def discover_menu_candidates(self, screen_data: dict[str, Any]) -> list[dict[str, Any]]:
        custom_interactives = screen_data.get("custom_interactives", [])

        candidates: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for item in custom_interactives:
            text = self._clean_text(item.get("text", ""))
            tag = (item.get("tag") or "").lower().strip()
            selector = item.get("selector", "")

            if not self._is_valid_menu_text(text):
                continue

            if not self._is_menu_tag(tag):
                continue

            key = self._normalize_key(text)

            if key in seen_keys:
                continue

            seen_keys.add(key)

            candidates.append(
                {
                    "label": text,
                    "tag": tag,
                    "selector": selector,
                    "kind": self._kind_for_tag(tag),
                    "reason": "custom_navigation_item",
                }
            )

        return candidates

    def discover_collapsable_menu_candidates(
        self,
        screen_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidates = self.discover_menu_candidates(screen_data)

        return [
            candidate
            for candidate in candidates
            if candidate["kind"] == "collapsable_menu"
        ]

    def _is_menu_tag(self, tag: str) -> bool:
        return tag in self.COLLAPSABLE_TAGS or tag in self.BASIC_TAGS

    def _kind_for_tag(self, tag: str) -> str:
        if tag in self.COLLAPSABLE_TAGS:
            return "collapsable_menu"

        if tag in self.BASIC_TAGS:
            return "basic_menu_item"

        return "unknown_menu_item"

    def _clean_text(self, value: str) -> str:
        value = value or ""
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _normalize_key(self, value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"\s+", " ", value)
        return value

    def _is_valid_menu_text(self, text: str) -> bool:
        if not text:
            return False

        normalized = self._normalize_key(text)

        if normalized in self.IGNORED_TEXTS:
            return False

        if normalized.startswith("menu de navegación"):
            return False

        if normalized.startswith("menu de navegacion"):
            return False

        # Evita capturar contenedores gigantes que contienen todo el menú.
        if len(text) > 80:
            return False

        return True