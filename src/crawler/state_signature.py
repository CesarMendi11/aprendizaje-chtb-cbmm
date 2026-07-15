from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class StateSignature:
    """
    Firmas exacta y estructural de un estado UI.

    ``fingerprint`` se mantiene como alias de la firma estructural para conservar
    compatibilidad con el crawler existente. La firma estructural ignora ruido
    volátil; la exacta permite auditar cambios de contenido.
    """

    fingerprint: str
    exact_fingerprint: str
    structural_fingerprint: str
    route: str
    title: str
    summary: dict[str, Any]
    exact_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "exact_fingerprint": self.exact_fingerprint,
            "structural_fingerprint": self.structural_fingerprint,
            "route": self.route,
            "title": self.title,
            "summary": self.summary,
            "exact_summary": self.exact_summary,
        }


class StateSignatureBuilder:
    """
    Construye una identidad estable para estados de interfaz.

    La firma exacta conserva datos observables normalizados. La firma
    estructural elimina valores que suelen cambiar sin representar un nuevo
    estado funcional: fechas, horas, UUID, tokens, identificadores largos,
    valores de consulta y cantidad de filas.
    """

    DEFAULT_VOLATILE_PATTERNS = (
        # UUID.
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        # Fechas ISO y fechas comunes en español.
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        # Horas.
        r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?\b",
        # Tokens/hexadecimales largos.
        r"\b[0-9a-f]{24,}\b",
        # Identificadores numéricos largos; se conservan números cortos de UI.
        r"\b\d{6,}\b",
    )

    def __init__(
        self,
        visible_text_limit: int = 2000,
        volatile_text_patterns: list[str] | tuple[str, ...] | None = None,
        ignore_query_values: bool = True,
        ignore_table_row_count: bool = True,
    ):
        self.visible_text_limit = visible_text_limit
        self.volatile_text_patterns = tuple(
            volatile_text_patterns or self.DEFAULT_VOLATILE_PATTERNS
        )
        self.ignore_query_values = ignore_query_values
        self.ignore_table_row_count = ignore_table_row_count

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> "StateSignatureBuilder":
        config = profile.get("state_detection", {})
        extraction = profile.get("extraction", {})

        return cls(
            visible_text_limit=int(
                config.get(
                    "visible_text_limit",
                    extraction.get("max_visible_text_chars", 2000),
                )
            ),
            volatile_text_patterns=config.get("volatile_text_patterns"),
            ignore_query_values=bool(config.get("ignore_query_values", True)),
            ignore_table_row_count=bool(
                config.get("ignore_table_row_count", True)
            ),
        )

    def build(self, screen_data: dict[str, Any]) -> StateSignature:
        route = screen_data.get("path") or ""
        title = screen_data.get("title") or ""

        exact_summary = self._build_summary(screen_data, structural=False)
        structural_summary = self._build_summary(screen_data, structural=True)

        exact_fingerprint = self._hash(exact_summary)
        structural_fingerprint = self._hash(structural_summary)

        return StateSignature(
            # Compatibilidad: el crawler compara el estado funcional, no datos.
            fingerprint=structural_fingerprint,
            exact_fingerprint=exact_fingerprint,
            structural_fingerprint=structural_fingerprint,
            route=route,
            title=title,
            summary=structural_summary,
            exact_summary=exact_summary,
        )

    def has_changed(
        self,
        before: StateSignature,
        after: StateSignature,
        mode: str = "structural",
    ) -> bool:
        if mode == "exact":
            return before.exact_fingerprint != after.exact_fingerprint
        return before.structural_fingerprint != after.structural_fingerprint

    def _build_summary(
        self,
        screen_data: dict[str, Any],
        structural: bool,
    ) -> dict[str, Any]:
        visible_text = (screen_data.get("visible_text") or "")[
            : self.visible_text_limit
        ]

        if structural:
            visible_text = self._normalize_structural_text(visible_text)
        else:
            visible_text = self._normalize_text(visible_text)

        return {
            "route": self._normalize_route(
                screen_data.get("path") or "",
                structural=structural,
            ),
            "title": self._normalize_text(screen_data.get("title")),
            "visible_text": visible_text,
            "links": self._normalize_links(
                screen_data.get("links", []),
                structural=structural,
            ),
            "buttons": self._normalize_buttons(screen_data.get("buttons", [])),
            "inputs": self._normalize_inputs(screen_data.get("inputs", [])),
            "tables": self._normalize_tables(
                screen_data.get("tables", []),
                structural=structural,
            ),
            "custom_interactives": self._normalize_custom_interactives(
                screen_data.get("custom_interactives", [])
            ),
            "dialogs": self._normalize_dialogs(screen_data.get("dialogs", [])),
        }

    def _normalize_links(
        self,
        links: list[dict[str, Any]],
        structural: bool,
    ) -> list[dict[str, str]]:
        normalized = []

        for item in links:
            normalized.append(
                {
                    "text": self._normalize_text(item.get("text")),
                    "href": self._normalize_route(
                        item.get("href") or "",
                        structural=structural,
                    ),
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
                    "aria_expanded": self._normalize_text(
                        item.get("aria_expanded")
                    ),
                    "aria_selected": self._normalize_text(
                        item.get("aria_selected")
                    ),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_inputs(self, inputs: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized = []

        for item in inputs:
            normalized.append(
                {
                    "name": self._normalize_text(item.get("name")),
                    "id": self._normalize_technical_id(item.get("id")),
                    "type": self._normalize_text(item.get("type")),
                    "placeholder": self._normalize_text(item.get("placeholder")),
                    "label": self._normalize_text(item.get("label")),
                    "tag": self._normalize_text(item.get("tag")),
                    "required": str(bool(item.get("required"))).lower(),
                    "disabled": str(bool(item.get("disabled"))).lower(),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_tables(
        self,
        tables: list[dict[str, Any]],
        structural: bool,
    ) -> list[dict[str, Any]]:
        normalized = []

        for item in tables:
            headers = item.get("headers") or []
            table = {
                "headers": [
                    self._normalize_text(header)
                    for header in headers
                    if self._normalize_text(header)
                ],
            }

            if not structural or not self.ignore_table_row_count:
                table["rows_count"] = int(item.get("rows_count") or 0)

            normalized.append(table)

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
                    "aria_selected": self._normalize_text(item.get("aria_selected")),
                    "aria_hidden": self._normalize_text(item.get("aria_hidden")),
                    "onclick": str(bool(item.get("onclick"))).lower(),
                }
            )

        return self._sort_unique(normalized)

    def _normalize_dialogs(self, dialogs: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized = []
        for item in dialogs:
            normalized.append(
                {
                    "title": self._normalize_text(item.get("title")),
                    "role": self._normalize_text(item.get("role")),
                    "open": str(bool(item.get("open", True))).lower(),
                }
            )
        return self._sort_unique(normalized)

    def _normalize_route(self, value: Any, structural: bool) -> str:
        route = self._normalize_text(value)
        if not route:
            return ""

        if not structural or not self.ignore_query_values:
            return route

        parsed = urlsplit(route)
        if not parsed.query:
            return route

        query_keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
        normalized_query = urlencode([(key, "*") for key in query_keys])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, normalized_query, ""))

    def _normalize_structural_text(self, value: Any) -> str:
        text = self._normalize_text(value)
        for pattern in self.volatile_text_patterns:
            try:
                text = re.sub(pattern, "<volatile>", text, flags=re.IGNORECASE)
            except re.error:
                # Un patrón configurable inválido no debe romper el crawler.
                continue
        text = re.sub(r"(?:<volatile>\s*){2,}", "<volatile> ", text)
        return text.strip()

    def _normalize_technical_id(self, value: Any) -> str:
        text = self._normalize_text(value)
        if not text:
            return ""

        # Angular y otros frameworks suelen agregar sufijos numéricos variables.
        text = re.sub(r"([_-])\d{4,}$", r"\1<volatile>", text)
        return text

    def _normalize_text(self, value: Any) -> str:
        if value is None:
            return ""

        text = str(value)
        text = re.sub(r"\s+", " ", text)
        return text.strip().lower()

    def _hash(self, summary: dict[str, Any]) -> str:
        payload = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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

        return [unique[key] for key in sorted(unique.keys())]
