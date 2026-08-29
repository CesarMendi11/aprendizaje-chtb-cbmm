from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class PipelineSettings:
    """Environment-backed paths shared by governed pipeline executors."""

    crawl_profile_path: Path = field(
        default_factory=lambda: Path(
            os.getenv("ERP_ASSISTANT_CRAWL_PROFILE", "configs/cbmm.yaml")
        )
    )
    runs_root: Path = field(
        default_factory=lambda: Path(
            os.getenv("ERP_ASSISTANT_PIPELINE_RUNS_DIR", "data/runs/pipeline")
        )
    )
