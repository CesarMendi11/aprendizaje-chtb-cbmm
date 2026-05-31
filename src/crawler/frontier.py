from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class CrawlTarget:
    """
    Representa una ruta pendiente de exploración.

    route:
        Ruta interna del ERP. Ejemplo: /admin/facturas

    source:
        Ruta desde donde fue descubierta. Ejemplo: /admin/home

    depth:
        Profundidad de exploración.

    reason:
        Motivo por el cual se agregó a la cola.
    """

    route: str
    source: str
    depth: int = 0
    reason: str = "discovered"


class Frontier:
    """
    Cola de exploración del crawler.

    Responsabilidad:
    - Guardar rutas pendientes.
    - Evitar rutas duplicadas.
    - Registrar rutas visitadas.
    - Entregar el siguiente objetivo a explorar.

    No navega.
    No usa Playwright.
    No extrae pantallas.
    """

    def __init__(self):
        self._queue: deque[CrawlTarget] = deque()
        self._queued_routes: set[str] = set()
        self._visited_routes: set[str] = set()

    def push(self, target: CrawlTarget) -> bool:
        """
        Agrega una ruta a la cola si no está pendiente ni visitada.

        Retorna True si fue agregada.
        Retorna False si fue ignorada.
        """

        if target.route in self._visited_routes:
            return False

        if target.route in self._queued_routes:
            return False

        self._queue.append(target)
        self._queued_routes.add(target.route)

        return True

    def pop(self) -> CrawlTarget | None:
        """
        Devuelve el siguiente objetivo pendiente.
        """

        if not self._queue:
            return None

        target = self._queue.popleft()
        self._queued_routes.discard(target.route)

        return target

    def mark_visited(self, route: str) -> None:
        """
        Marca una ruta como visitada.
        """

        self._visited_routes.add(route)
        self._queued_routes.discard(route)

    def is_visited(self, route: str) -> bool:
        return route in self._visited_routes

    def is_queued(self, route: str) -> bool:
        return route in self._queued_routes

    def has_pending(self) -> bool:
        return bool(self._queue)

    def pending_count(self) -> int:
        return len(self._queue)

    def visited_count(self) -> int:
        return len(self._visited_routes)

    def queued_routes(self) -> list[str]:
        return list(self._queued_routes)

    def visited_routes(self) -> list[str]:
        return list(self._visited_routes)

    def clear(self) -> None:
        self._queue.clear()
        self._queued_routes.clear()
        self._visited_routes.clear()