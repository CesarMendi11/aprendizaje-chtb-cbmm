"""Modelos internos del crawler y del conocimiento estructural."""

from src.models.crawl_path import CrawlPath, CrawlPathStep
from src.models.transition import Transition
from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from src.models.ui_state import UIState

__all__ = [
    "CrawlPath",
    "CrawlPathStep",
    "EventDecision",
    "RiskLevel",
    "Transition",
    "UIEvent",
    "UIEventType",
    "UIState",
]
