"""Modelos internos del crawler y del conocimiento estructural."""

from erp_assistant.acquisition.models.crawl_path import CrawlPath, CrawlPathStep
from erp_assistant.acquisition.models.transition import Transition
from erp_assistant.acquisition.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from erp_assistant.acquisition.models.ui_state import UIState

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
