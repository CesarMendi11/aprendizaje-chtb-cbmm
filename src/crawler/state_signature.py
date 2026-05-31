from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StateSignature:
    """
    Representa una firma estable del estado UI actual.

    fingerprint:
        Hash único del estado resumido.

    route:
        Ruta actual.

    title:
        Título de la pantalla.

    summary:
        Resumen normalizado usado para construir la firma.
    """

    fingerprint: str
    route: str
    title: str
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "route": self.route,
            "title": self.title,
            "summary": self.summary,
        }


class StateSignatureBuilder:
    """
    Construye una firma del estado actual de la interfaz.

    Responsabilidad:
    - Tomar screen_data.
    - Normalizar datos relevantes.
    - Ignorar ruido visual o valores volátiles.
    - Generar un hash estable.

    Este componente NO navega.
    Este componente NO hace click.
    Este componente NO decide qué explorar.
    """

    def __init__(self, visible_text_limit: int = 2000):
        self.visible_text_limit = visible_text_limit

    def build(self, screen_data: dict[str, Any]) -> StateSignature:
        route = screen_data.get("path") or ""
        title = screen_data.get("title") or ""

        summary = {
            "route": self._normalize_text(route),
            "title": self._normalize_text(title),
            "visible_text": self._normalize_text(
                (screen_data.get("visible_text") or "")[: self.visible_text_limit]
            ),
            "links": self._normalize_links(screen_data.get("links", [])),
            "buttons": self._normalize_buttons(screen_data.get("buttons", [])),
            "inputs": self._normalize_inputs(screen_data.get("inputs", [])),
            "tables": self._normalize_tables(screen_data.get("tables", [])),
            "custom_interactives": self._normalize_custom_interactives(
                screen_data.get("custom_interactives", [])
            ),
        }

        payload = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()

        return StateSignature(
            fingerprint=fingerprint,
            route=route,
            title=title,
            summary=summary,
        )

    def has_changed(
        self,
        before: StateSignature,
        after: StateSignature,
    ) -> bool:
        return before.fingerprint != after.fingerprint

    def _normalize_links(self, links: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized = []

        for item in links:
            normalized.append(
                {
                    "text": self._normalize_text(item.get("text")),
                    "href": self._normalize_text(item.get("href")),
                    "tag": self._normalize_text(item.get("tag")),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_buttons(self, buttons: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized = []

        for item in buttons:
            normalized.append(
                {
                    "text": self._normalize_text(item.get("text")),
                    "type": self._normalize_text(item.get("type")),
                    "role": self._normalize_text(item.get("role")),
                    "tag": self._normalize_text(item.get("tag")),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_inputs(self, inputs: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized = []

        for item in inputs:
            normalized.append(
                {
                    "name": self._normalize_text(item.get("name")),
                    "id": self._normalize_text(item.get("id")),
                    "type": self._normalize_text(item.get("type")),
                    "placeholder": self._normalize_text(item.get("placeholder")),
                    "label": self._normalize_text(item.get("label")),
                    "tag": self._normalize_text(item.get("tag")),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_tables(self, tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []

        for item in tables:
            headers = item.get("headers") or []

            normalized.append(
                {
                    "headers": [
                        self._normalize_text(header)
                        for header in headers
                        if self._normalize_text(header)
                    ],
                    "rows_count": int(item.get("rows_count") or 0),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_custom_interactives(
        self,
        custom_interactives: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        normalized = []

        for item in custom_interactives:
            normalized.append(
                {
                    "text": self._normalize_text(item.get("text")),
                    "tag": self._normalize_text(item.get("tag")),
                    "role": self._normalize_text(item.get("role")),
                    "aria_expanded": self._normalize_text(item.get("aria_expanded")),
                    "onclick": str(bool(item.get("onclick"))).lower(),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_text(self, value: Any) -> str:
        if value is None:
            return ""

        text = str(value)
        text = re.sub(r"\s+", " ", text)
        return text.strip().lower()

    def _sort_unique(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}

        for item in items:
            key = json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            unique[key] = item

        return [
            unique[key]
            for key in sorted(unique.keys())
        ]