from __future__ import annotations

from typing import Any

from src.models.transition import Transition
from src.models.ui_state import UIState


class StateFlowGraphBuilder:
    """Construye el grafo de estados y eventos observado por el crawler."""

    def __init__(self):
        self._states: dict[str, dict[str, Any]] = {}
        self._transitions: list[dict[str, Any]] = []
        self._transition_keys: set[tuple[str, str, str, str]] = set()

    def add_state(self, state: UIState) -> None:
        self._states[state.state_id] = state.to_dict()

    def add_transition(self, transition: Transition) -> bool:
        key = (
            transition.source_state_id,
            transition.target_state_id,
            transition.event.event_type.value,
            transition.event.selector,
        )
        if key in self._transition_keys:
            return False
        self._transition_keys.add(key)
        self._transitions.append(transition.to_dict())
        return True

    def state_count(self) -> int:
        return len(self._states)

    def transition_count(self) -> int:
        return len(self._transitions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_type": "erp_ui_state_flow_graph",
            "states": list(self._states.values()),
            "transitions": self._transitions,
            "summary": {
                "states_count": self.state_count(),
                "transitions_count": self.transition_count(),
            },
        }
