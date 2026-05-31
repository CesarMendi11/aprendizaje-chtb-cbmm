from __future__ import annotations

from src.discovery.link_normalizer import LinkNormalizer
from src.policy.route_policy import RoutePolicy


class LinkDiscovery:
    """
    Descubre rutas candidatas a partir de los links extraídos.

    Responsabilidad:
    - Recibir screen_data.
    - Normalizar links.
    - Filtrar por política de rutas.
    - Devolver rutas candidatas para el crawler.

    No navega.
    No guarda archivos.
    No ejecuta Playwright.
    """

    def __init__(self, policy: RoutePolicy):
        self.policy = policy
        self.normalizer = LinkNormalizer(policy)

    def discover_allowed_links(self, screen_data: dict) -> list[dict]:
        raw_links = screen_data.get("links", [])
        normalized_links = self.normalizer.normalize_many(raw_links)

        allowed_links = []

        for link in normalized_links:
            route = link["route"]

            if not self.policy.is_allowed_route(route):
                continue

            allowed_links.append(link)

        return allowed_links

    def discover_allowed_routes(self, screen_data: dict) -> list[str]:
        links = self.discover_allowed_links(screen_data)

        routes = []
        seen = set()

        for link in links:
            route = link["route"]

            if route in seen:
                continue

            seen.add(route)
            routes.append(route)

        return routes