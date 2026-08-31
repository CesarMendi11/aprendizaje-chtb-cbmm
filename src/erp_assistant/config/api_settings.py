from __future__ import annotations

import os
from dataclasses import dataclass, field


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
    reload: bool = field(default_factory=lambda: os.getenv("API_RELOAD") == "1")
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
