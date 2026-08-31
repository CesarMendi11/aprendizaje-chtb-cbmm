from __future__ import annotations

from urllib.parse import urljoin, urlparse


class RoutePolicy:
    """
    Política de navegación del crawler.

    Responsabilidad:
    - Permitir o bloquear rutas.
    - Normalizar enlaces encontrados en el ERP.
    - Detectar acciones peligrosas.
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.base_url = profile["erp"]["base_url"].rstrip("/")

        exploration = profile.get("exploration", {})
        safety = profile.get("safety", {})

        self.allowed_routes: list[str] = exploration.get("allowed_routes", [])
        self.blocked_routes: list[str] = exploration.get("blocked_routes", [])

        self.dangerous_keywords = [
            keyword.lower().strip()
            for keyword in safety.get("dangerous_keywords", [])
            if keyword
        ]

        self.safe_keywords = [
            keyword.lower().strip()
            for keyword in safety.get("safe_keywords", [])
            if keyword
        ]

    def normalize_href(self, href: str | None) -> str | None:
        """
        Convierte un href relativo o absoluto en ruta interna.

        Ejemplos:
        /admin/home -> /admin/home
        http://localhost:8080/admin/home -> /admin/home
        javascript:void(0) -> None
        """

        if not href:
            return None

        href = href.strip()

        if not href:
            return None

        invalid_prefixes = ("javascript:", "mailto:", "tel:", "#")

        if href.lower().startswith(invalid_prefixes):
            return None

        absolute_url = urljoin(self.base_url + "/", href)

        parsed_base = urlparse(self.base_url)
        parsed_target = urlparse(absolute_url)

        if parsed_target.netloc and parsed_target.netloc != parsed_base.netloc:
            return None

        route = parsed_target.path or "/"

        if parsed_target.query:
            route = f"{route}?{parsed_target.query}"

        return route

    def is_allowed_route(self, route: str | None) -> bool:
        if not route:
            return False

        route = route.strip()

        if route == "/":
            return False

        for blocked_route in self.blocked_routes:
            if blocked_route and route.startswith(blocked_route):
                return False

        if not self.allowed_routes:
            return True

        return any(route.startswith(allowed_route) for allowed_route in self.allowed_routes)

    def is_dangerous_action_label(self, label: str | None) -> bool:
        if not label:
            return False

        normalized = label.lower().strip()

        return any(keyword in normalized for keyword in self.dangerous_keywords)

    def is_safe_action_label(self, label: str | None) -> bool:
        if not label:
            return False

        normalized = label.lower().strip()

        if self.is_dangerous_action_label(normalized):
            return False

        if not self.safe_keywords:
            return True

        return any(keyword in normalized for keyword in self.safe_keywords)