from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


class DatabaseConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class DatabaseSettings:
    url: str | None = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_DATABASE_URL") or None
    )
    echo: bool = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_DATABASE_ECHO", "false").casefold()
        in {"1", "true", "yes", "on"}
    )
    pool_size: int = field(
        default_factory=lambda: int(os.getenv("ERP_ASSISTANT_DATABASE_POOL_SIZE", "5"))
    )
    max_overflow: int = field(
        default_factory=lambda: int(os.getenv("ERP_ASSISTANT_DATABASE_MAX_OVERFLOW", "5"))
    )
    create_sync_jobs: bool = field(
        default_factory=lambda: os.getenv(
            "ERP_ASSISTANT_CREATE_SYNC_JOBS", "true"
        ).casefold()
        in {"1", "true", "yes", "on"}
    )

    def require_url(self, *, postgresql: bool = True) -> str:
        if not self.url:
            raise DatabaseConfigurationError(
                "Falta ERP_ASSISTANT_DATABASE_URL. Configure una URL PostgreSQL."
            )
        if postgresql and not self.url.startswith(("postgresql+psycopg://", "postgresql://")):
            raise DatabaseConfigurationError(
                "ERP_ASSISTANT_DATABASE_URL debe usar PostgreSQL con psycopg."
            )
        return self.url

    @property
    def safe_url(self) -> str:
        if not self.url:
            return "<no configurada>"
        parts = urlsplit(self.url)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        netloc = f"{user}:***@{host}" if user else host
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

