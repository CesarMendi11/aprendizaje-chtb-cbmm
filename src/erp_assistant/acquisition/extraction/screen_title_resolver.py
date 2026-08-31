from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote


@dataclass(frozen=True)
class ResolvedScreenTitle:
    """Título funcional seleccionado junto con su procedencia."""

    title: str
    source: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "source": self.source,
            "confidence": self.confidence,
        }


class ScreenTitleResolver:
    """Resuelve nombres funcionales sin depender de ``document.title``.

    Los ERP tipo SPA suelen mantener el mismo título HTML en todas sus rutas.
    El resolvedor combina estrategias genéricas y una pista observada durante
    la navegación. Las excepciones propias de un ERP permanecen en el YAML.
    """

    DEFAULT_GENERIC_TITLES = {
        "dashboard",
        "erp",
        "inicio",
        "home",
        "aplicación",
        "aplicacion",
        "sistema",
    }

    SOURCE_CONFIDENCE = {
        "route_override": 1.0,
        "main_heading": 0.98,
        "page_title": 0.95,
        "breadcrumb": 0.92,
        "discovery_hint": 0.90,
        "active_navigation": 0.86,
        "document_title": 0.60,
        "route_fallback": 0.55,
        "unknown": 0.0,
    }

    def __init__(self, profile: dict[str, Any]):
        config = profile.get("extraction", {}).get("title_resolution", {})
        self.route_titles = {
            str(route): self._clean(title)
            for route, title in (config.get("route_titles") or {}).items()
            if self._clean(title)
        }
        configured_generic = {
            self._normalize(value)
            for value in config.get("generic_document_titles", [])
        }
        self.generic_titles = {
            *{self._normalize(value) for value in self.DEFAULT_GENERIC_TITLES},
            *configured_generic,
        }
        self.max_title_length = int(config.get("max_title_length", 120))

    def resolve(
        self,
        screen_data: dict[str, Any],
        title_hint: str | None = None,
    ) -> ResolvedScreenTitle:
        route = str(screen_data.get("path") or "")

        configured = self.route_titles.get(route)
        if configured:
            return self._result(configured, "route_override")

        candidates: list[tuple[int, str, str]] = []
        for candidate in screen_data.get("title_candidates", []):
            text = self._clean(candidate.get("text"))
            source = str(candidate.get("source") or "unknown")
            score = int(candidate.get("score") or 0)
            if self._is_usable(text):
                if self._is_generic(text):
                    score -= 60
                candidates.append((score, source, text))

        hint = self._clean(title_hint)
        if self._is_usable(hint):
            candidates.append((85, "discovery_hint", hint))

        document_title = self._clean(
            screen_data.get("document_title") or screen_data.get("title")
        )
        if self._is_usable(document_title) and not self._is_generic(document_title):
            candidates.append((55, "document_title", document_title))

        if candidates:
            candidates.sort(
                key=lambda item: (
                    item[0],
                    self.SOURCE_CONFIDENCE.get(item[1], 0.0),
                    -len(item[2]),
                ),
                reverse=True,
            )
            _, source, title = candidates[0]
            return self._result(title, source)

        fallback = self._title_from_route(route)
        if fallback:
            return self._result(fallback, "route_fallback")

        if document_title:
            return self._result(document_title, "document_title")

        return self._result("Pantalla sin título", "unknown")

    def _result(self, title: str, source: str) -> ResolvedScreenTitle:
        return ResolvedScreenTitle(
            title=self._clean(title),
            source=source,
            confidence=self.SOURCE_CONFIDENCE.get(source, 0.0),
        )

    def _title_from_route(self, route: str) -> str:
        clean_route = route.split("?", 1)[0].rstrip("/")
        if not clean_route:
            return ""
        segment = unquote(clean_route.rsplit("/", 1)[-1])
        segment = re.sub(r"[-_]+", " ", segment)
        segment = re.sub(r"\s+", " ", segment).strip()
        if not segment:
            return ""
        return segment[:1].upper() + segment[1:]

    def _is_usable(self, value: str) -> bool:
        if not value or len(value) > self.max_title_length:
            return False
        return len(value.split()) <= 14

    def _is_generic(self, value: str) -> bool:
        return self._normalize(value) in self.generic_titles

    def _clean(self, value: Any) -> str:
        if value is None:
            return ""
        return re.sub(r"\s+", " ", str(value)).strip()

    def _normalize(self, value: Any) -> str:
        return self._clean(value).casefold()
