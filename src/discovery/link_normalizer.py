from __future__ import annotations

from src.policy.route_policy import RoutePolicy


class LinkNormalizer:
    """
    Normaliza enlaces encontrados en pantalla.

    Responsabilidad:
    - Tomar hrefs crudos del extractor.
    - Convertirlos a rutas internas.
    - Eliminar duplicados.
    - No decide si una ruta se visita o no; eso lo decide RoutePolicy.
    """

    def __init__(self, policy: RoutePolicy):
        self.policy = policy

    def normalize_many(self, links: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        seen_routes: set[str] = set()

        for link in links:
            href = link.get("href") or link.get("absolute_href")
            route = self.policy.normalize_href(href)

            if not route:
                continue

            if route in seen_routes:
                continue

            seen_routes.add(route)

            normalized.append(
                {
                    "route": route,
                    "text": link.get("text", ""),
                    "href": href,
                    "selector": link.get("selector", ""),
                    "tag": link.get("tag", ""),
                    "region": link.get("region", "main_content"),
                }
            )

        return normalized