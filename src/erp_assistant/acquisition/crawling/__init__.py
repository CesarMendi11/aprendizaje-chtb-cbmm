"""Motores de recorrido por rutas y por estados de interfaz."""

from erp_assistant.acquisition.crawling.module_scope import (
    ModuleCrawlBoundary,
    ModuleCrawlBoundaryError,
    ModuleNavigationStep,
)
from erp_assistant.acquisition.crawling.path_replayer import PathReplayer, ReplayResult
from erp_assistant.acquisition.crawling.state_frontier import StateFrontier, StateTarget
from erp_assistant.acquisition.crawling.state_registry import StateRegistration, StateRegistry
from erp_assistant.acquisition.crawling.state_restorer import RestoreResult, StateRestorer

__all__ = [
    "ModuleCrawlBoundary",
    "ModuleCrawlBoundaryError",
    "ModuleNavigationStep",
    "PathReplayer",
    "ReplayResult",
    "RestoreResult",
    "StateFrontier",
    "StateRegistration",
    "StateRegistry",
    "StateRestorer",
    "StateTarget",
]
