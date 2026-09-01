from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from erp_assistant.config.paths import PROJECT_ROOT
from erp_assistant.persistence.postgres.session import database_engine as database_engine


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def json_default(value: Any):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (Path, Enum)):
        return str(value)
    return str(value)


def print_json(value: Any, *, pretty: bool = False):
    print(json.dumps(value, default=json_default, ensure_ascii=False, indent=2 if pretty else None))
