from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import ValidationError

from src.knowledge.models import StructuralScreen

logger = logging.getLogger(__name__)


class StructuralKnowledgeRepository:
    def __init__(self, screen_index_path: Path):
        self.screen_index_path = Path(screen_index_path)
        self._screens: tuple[StructuralScreen, ...] = ()
        self._by_route: dict[str, StructuralScreen] = {}
        self.load_error: str | None = None

    @property
    def knowledge_loaded(self) -> bool:
        return bool(self._screens) and self.load_error is None

    @property
    def screens_count(self) -> int:
        return len(self._screens)

    @property
    def screens(self) -> tuple[StructuralScreen, ...]:
        return self._screens

    def get_by_route(self, route: str | None) -> StructuralScreen | None:
        if not route:
            return None
        clean_route = route.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        return self._by_route.get(clean_route)

    def reload(self) -> bool:
        return self.load()

    def load(self) -> bool:
        try:
            payload = json.loads(self.screen_index_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("screens"), list):
                raise ValueError("screen_index debe contener una lista 'screens'")
            screens = tuple(StructuralScreen.model_validate(item) for item in payload["screens"])
            if not screens:
                raise ValueError("screen_index no contiene pantallas")
            routes = [screen.route.rstrip("/") or "/" for screen in screens]
            if len(routes) != len(set(routes)):
                raise ValueError("screen_index contiene rutas duplicadas")
        except (OSError, json.JSONDecodeError, UnicodeError, ValueError, ValidationError) as exc:
            self._screens = ()
            self._by_route = {}
            self.load_error = type(exc).__name__
            logger.warning("No se pudo cargar el conocimiento estructural (%s)", type(exc).__name__)
            return False

        self._screens = screens
        self._by_route = dict(zip(routes, screens, strict=True))
        self.load_error = None
        logger.info("Conocimiento estructural cargado: %d pantallas", len(screens))
        return True
