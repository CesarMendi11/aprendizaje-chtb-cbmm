"""Motores de recorrido por rutas y por estados de interfaz."""

from src.crawler.module_scope import (
    ModuleCrawlBoundary,
    ModuleCrawlBoundaryError,
    ModuleNavigationStep,
)
from src.crawler.path_replayer import PathReplayer, ReplayResult
from src.crawler.state_frontier import StateFrontier, StateTarget
from src.crawler.state_registry import StateRegistration, StateRegistry
from src.crawler.state_restorer import RestoreResult, StateRestorer

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
