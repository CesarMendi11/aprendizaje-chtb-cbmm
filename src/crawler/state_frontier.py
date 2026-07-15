from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from src.models.crawl_path import CrawlPath
from src.models.ui_state import UIState


@dataclass(frozen=True)
class StateTarget:
    """Estado pendiente de exploración dentro del state-flow graph."""

    state_id: str
    path: CrawlPath
    depth: int
    source_state_id: str | None = None
    reason: str = "discovered"


class StateFrontier:
    """
    Frontera FIFO de estados UI.

    A diferencia de ``Frontier``, que deduplica rutas, esta frontera distingue
    múltiples estados funcionales dentro de una misma URL.
    """

    def __init__(self):
        self._queue: deque[StateTarget] = deque()
        self._queued_state_ids: set[str] = set()
        self._explored_state_ids: set[str] = set()

    def push(self, target: StateTarget) -> bool:
        if not target.state_id:
            raise ValueError("target.state_id no puede estar vacío.")
        if target.depth < 0:
            raise ValueError("target.depth no puede ser negativo.")
        if target.state_id in self._explored_state_ids:
            return False
        if target.state_id in self._queued_state_ids:
            return False

        self._queue.append(target)
        self._queued_state_ids.add(target.state_id)
        return True

    def push_state(
        self,
        state: UIState,
        source_state_id: str | None = None,
        reason: str = "discovered",
    ) -> bool:
        if state.path is None:
            raise ValueError("El estado debe tener una trayectoria reproducible.")
        return self.push(
            StateTarget(
                state_id=state.state_id,
                path=state.path,
                depth=state.path.depth,
                source_state_id=source_state_id,
                reason=reason,
            )
        )

    def pop(self) -> StateTarget | None:
        if not self._queue:
            return None
        target = self._queue.popleft()
        self._queued_state_ids.discard(target.state_id)
        return target

    def mark_explored(self, state_id: str) -> None:
        self._explored_state_ids.add(state_id)
        self._queued_state_ids.discard(state_id)

    def is_explored(self, state_id: str) -> bool:
        return state_id in self._explored_state_ids

    def is_queued(self, state_id: str) -> bool:
        return state_id in self._queued_state_ids

    def has_pending(self) -> bool:
        return bool(self._queue)

    def pending_count(self) -> int:
        return len(self._queue)

    def explored_count(self) -> int:
        return len(self._explored_state_ids)

    def queued_state_ids(self) -> list[str]:
        return list(self._queued_state_ids)

    def explored_state_ids(self) -> list[str]:
        return list(self._explored_state_ids)

    def clear(self) -> None:
        self._queue.clear()
        self._queued_state_ids.clear()
        self._explored_state_ids.clear()
