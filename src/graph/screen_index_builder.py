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
            "title": (
                screen_data.get("functional_title")
                or screen_data.get("title", "")
            ),
            "document_title": screen_data.get("document_title", ""),
            "functional_title": screen_data.get("functional_title", ""),
            "title_source": screen_data.get("title_source", ""),
            "title_confidence": screen_data.get("title_confidence", 0.0),
            "visible_text": screen_data.get("visible_text", ""),
            "main_visible_text": screen_data.get("main_visible_text", ""),
            "regions": screen_data.get("regions", {}),
            "visible_text_truncated": screen_data.get("visible_text_truncated", False),
            "links": screen_data.get("links", []),
            "buttons": screen_data.get("buttons", []),
            "inputs": screen_data.get("inputs", []),
            "tables": screen_data.get("tables", []),
            "custom_interactives": screen_data.get("custom_interactives", []),
            "dialogs": screen_data.get("dialogs", []),
            "global_links": screen_data.get("global_links", []),
            "local_links": screen_data.get("local_links", []),
            "global_interactives": screen_data.get("global_interactives", []),
            "local_interactives": screen_data.get("local_interactives", []),
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