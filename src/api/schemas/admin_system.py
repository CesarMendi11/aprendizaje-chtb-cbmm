from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AdminSystemStatusResponse(BaseModel):
    """Snapshot de observabilidad de la consola administrativa."""

    ok: bool
    generated_at: datetime
    services: dict[str, dict[str, Any]]
    knowledge: dict[str, Any]
