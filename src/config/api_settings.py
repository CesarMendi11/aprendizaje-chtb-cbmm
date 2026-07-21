from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else PROJECT_ROOT / value


@dataclass(frozen=True)
class ApiSettings:
    semantic_review_api_enabled: bool = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_SEMANTIC_REVIEW_API") == "1"
    )
    semantic_review_allow_remote: bool = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_SEMANTIC_REVIEW_ALLOW_REMOTE") == "1"
    )
    host: str = field(default_factory=lambda: os.getenv("API_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            origin.strip()
            for origin in os.getenv(
                "API_CORS_ORIGINS",
                "http://localhost:4200,http://127.0.0.1:4200",
            ).split(",")
            if origin.strip() and origin.strip() != "*"
        )
    )
    screen_index_path: Path = field(
        default_factory=lambda: _path_from_env(
            "SCREEN_INDEX_PATH", "data/processed/structural/screen_index.json"
        )
    )
    routes_graph_path: Path = field(
        default_factory=lambda: _path_from_env(
            "ROUTES_GRAPH_PATH", "data/processed/structural/routes_graph.json"
        )
    )
    state_flow_graph_path: Path = field(
        default_factory=lambda: _path_from_env(
            "STATE_FLOW_GRAPH_PATH", "data/processed/structural/state_flow_graph.json"
        )
    )
    max_results: int = field(default_factory=lambda: int(os.getenv("SEARCH_MAX_RESULTS", "3")))
    minimum_score: float = field(
        default_factory=lambda: float(os.getenv("SEARCH_MINIMUM_SCORE", "2.5"))
    )
