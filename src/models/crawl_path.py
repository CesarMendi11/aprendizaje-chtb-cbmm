from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.ui_event import UIEvent


@dataclass(frozen=True)
class CrawlPathStep:
    """Paso reproducible de una trayectoria desde un estado hacia otro."""

    source_state_id: str
    event: UIEvent
    target_state_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_state_id": self.source_state_id,
            "event": self.event.to_dict(),
            "target_state_id": self.target_state_id,
        }


@dataclass(frozen=True)
class CrawlPath:
    """Trayectoria reproducible desde el estado raíz hasta un estado objetivo."""

    root_state_id: str
    steps: tuple[CrawlPathStep, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def depth(self) -> int:
        return len(self.steps)

    @property
    def target_state_id(self) -> str:
        if not self.steps:
            return self.root_state_id
        return self.steps[-1].target_state_id or self.steps[-1].source_state_id

    def append(self, step: CrawlPathStep) -> "CrawlPath":
        return CrawlPath(
            root_state_id=self.root_state_id,
            steps=(*self.steps, step),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_state_id": self.root_state_id,
            "target_state_id": self.target_state_id,
            "depth": self.depth,
            "steps": [step.to_dict() for step in self.steps],
            "metadata": self.metadata,
        }
