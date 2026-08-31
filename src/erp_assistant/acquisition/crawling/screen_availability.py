from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any


@dataclass(frozen=True)
class ScreenAvailabilityResult:
    available: bool
    status: str
    matched_patterns: tuple[str, ...] = ()
    text_field: str = "main_visible_text"

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "status": self.status,
            "matched_patterns": list(self.matched_patterns),
            "text_field": self.text_field,
        }


class ScreenAvailabilityClassifier:
    """Clasifica estados de pantalla no disponibles usando reglas deterministas del perfil."""

    def __init__(self, profile: dict[str, Any]):
        config = profile.get("screen_availability", {}) or {}
        self.enabled = bool(config.get("enabled", True))
        self.unavailable_status = str(
            config.get("unavailable_status", "unavailable") or "unavailable"
        ).strip()
        self.min_pattern_matches = max(
            1,
            int(config.get("min_pattern_matches", 1)),
        )
        self.patterns = tuple(
            str(item).strip()
            for item in (config.get("unavailable_text_patterns") or [])
            if isinstance(item, str) and item.strip()
        )

    def classify(self, screen_data: dict[str, Any]) -> ScreenAvailabilityResult:
        if not self.enabled or not self.patterns:
            return ScreenAvailabilityResult(available=True, status="available")

        main_text = str(screen_data.get("main_visible_text") or "").strip()
        visible_text = str(screen_data.get("visible_text") or "").strip()
        text_field = "main_visible_text" if main_text else "visible_text"
        normalized_text = self._normalize(main_text or visible_text)

        matched = tuple(
            pattern
            for pattern in self.patterns
            if self._normalize(pattern) in normalized_text
        )

        if len(matched) >= self.min_pattern_matches:
            return ScreenAvailabilityResult(
                available=False,
                status=self.unavailable_status,
                matched_patterns=matched,
                text_field=text_field,
            )

        return ScreenAvailabilityResult(
            available=True,
            status="available",
            matched_patterns=matched,
            text_field=text_field,
        )

    @staticmethod
    def _normalize(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or "").casefold())
        text = "".join(char for char in text if not unicodedata.combining(char))
        text = re.sub(r"\s+", " ", text)
        return text.strip()
