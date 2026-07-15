from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class UIEventType(StrEnum):
    """Categorías funcionales de eventos que puede observar el crawler."""

    NAVIGATION_LINK = "navigation_link"
    EXPAND_MENU = "expand_menu"
    COLLAPSE_MENU = "collapse_menu"
    ACTIVATE_TAB = "activate_tab"
    OPEN_READONLY_VIEW = "open_readonly_view"
    OPEN_MODAL = "open_modal"
    CLOSE_MODAL = "close_modal"
    OPEN_DRAWER = "open_drawer"
    CLOSE_DRAWER = "close_drawer"
    OPEN_DROPDOWN = "open_dropdown"
    CLOSE_DROPDOWN = "close_dropdown"
    EXPAND_ROW = "expand_row"
    SUBMIT_SEARCH = "submit_search"
    CHANGE_PAGINATION = "change_pagination"
    MUTATIVE_ACTION = "mutative_action"
    UNKNOWN = "unknown"


class EventDecision(StrEnum):
    """Decisión determinística de seguridad para un evento."""

    ALLOW = "allow"
    REVIEW = "review"
    DENY = "deny"


class RiskLevel(StrEnum):
    """Nivel de riesgo estimado antes de ejecutar el evento."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class UIEvent:
    """Descripción estable y serializable de una interacción de interfaz."""

    event_type: UIEventType
    label: str
    selector: str
    decision: EventDecision
    risk_level: RiskLevel
    source: str = ""
    tag: str = ""
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "label": self.label,
            "selector": self.selector,
            "decision": self.decision.value,
            "risk_level": self.risk_level.value,
            "source": self.source,
            "tag": self.tag,
            "reasons": list(self.reasons),
            "metadata": self.metadata,
        }
