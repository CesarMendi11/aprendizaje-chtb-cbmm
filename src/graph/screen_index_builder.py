from __future__ import annotations

from typing import Any


class ScreenIndexBuilder:
    """
    Construye un índice estructural de pantallas descubiertas.

    Responsabilidad:
    - Guardar resumen de cada pantalla.
    - Preparar datos para revisión humana, inferencia semántica y Neo4j.
    - Evitar duplicados por ruta.

    Este componente NO navega.
    Este componente NO extrae pantallas.
    Este componente NO guarda archivos.
    """

    def __init__(self):
        self._screens: dict[str, dict[str, Any]] = {}

    def add_screen(
        self,
        route: str,
        screen_data: dict[str, Any],
        status: str = "discovered",
    ) -> None:
        if not route:
            raise ValueError("route no puede estar vacío.")

        self._screens[route] = {
            "route": route,
            "url": screen_data.get("url"),
            "path": screen_data.get("path"),
            "title": screen_data.get("title", ""),
            "visible_text": screen_data.get("visible_text", ""),
            "visible_text_truncated": screen_data.get("visible_text_truncated", False),
            "links": screen_data.get("links", []),
            "buttons": screen_data.get("buttons", []),
            "inputs": screen_data.get("inputs", []),
            "tables": screen_data.get("tables", []),
            "custom_interactives": screen_data.get("custom_interactives", []),
            "artifacts": screen_data.get("artifacts", {}),
            "crawler": screen_data.get("crawler", {}),
            "status": status,
            "knowledge_origin": "discovered",
            "semantic_status": "pending",
        }

    def screen_count(self) -> int:
        return len(self._screens)

    def has_screen(self, route: str) -> bool:
        return route in self._screens

    def get_screen(self, route: str) -> dict[str, Any] | None:
        return self._screens.get(route)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_type": "erp_screen_index",
            "screens": list(self._screens.values()),
            "summary": {
                "screens_count": self.screen_count(),
            },
        }