from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.ui_event import UIEvent


@dataclass(frozen=True)
class Transition:
    """Arista observada entre dos estados de interfaz."""

    source_state_id: str
    target_state_id: str
    event: UIEvent
    changed_route: bool = False
    observed: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_state_id": self.source_state_id,
            "target_state_id": self.target_state_id,
            "event": self.event.to_dict(),
            "changed_route": self.changed_route,
            "observed": self.observed,
            "metadata": self.metadata,
        }
