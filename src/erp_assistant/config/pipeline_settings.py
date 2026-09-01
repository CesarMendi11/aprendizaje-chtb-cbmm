from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from erp_assistant.config.paths import PROJECT_ROOT


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()


@dataclass(frozen=True)
class PipelineSettings:
    """Environment-backed paths shared by governed pipeline executors."""

    crawl_profile_path: Path = field(
        default_factory=lambda: _path_from_env("ERP_ASSISTANT_CRAWL_PROFILE", "configs/cbmm.yaml")
    )
    runs_root: Path = field(
        default_factory=lambda: _path_from_env(
            "ERP_ASSISTANT_PIPELINE_RUNS_DIR", "data/runs/pipeline"
        )
    )
