from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.models.crawl_path import CrawlPath


@dataclass(frozen=True)
class UIState:
    """Estado funcional observable de una interfaz, independiente de la URL."""

    state_id: str
    route: str
    title: str
    exact_signature: str
    structural_signature: str
    summary: dict[str, Any]
    path: CrawlPath | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "route": self.route,
            "title": self.title,
            "exact_signature": self.exact_signature,
            "structural_signature": self.structural_signature,
            "summary": self.summary,
            "path": self.path.to_dict() if self.path else None,
            "metadata": self.metadata,
        }
