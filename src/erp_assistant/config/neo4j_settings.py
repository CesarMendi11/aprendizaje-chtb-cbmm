from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit


class Neo4jConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str | None = field(default_factory=lambda: os.getenv("ERP_ASSISTANT_NEO4J_URI") or None)
    user: str | None = field(default_factory=lambda: os.getenv("ERP_ASSISTANT_NEO4J_USER") or None)
    password: str | None = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_NEO4J_PASSWORD") or None
    )
    database: str = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_NEO4J_DATABASE", "neo4j")
    )

    def require(self) -> "Neo4jSettings":
        missing = [
            name
            for name, value in (("URI", self.uri), ("USER", self.user), ("PASSWORD", self.password))
            if not value
        ]
        if missing:
            raise Neo4jConfigurationError(
                "Falta configuración Neo4j: ERP_ASSISTANT_NEO4J_"
                + ", ERP_ASSISTANT_NEO4J_".join(missing)
            )
        if not self.uri.startswith(("bolt://", "neo4j://", "neo4j+s://", "neo4j+ssc://")):
            raise Neo4jConfigurationError("ERP_ASSISTANT_NEO4J_URI usa un esquema no soportado")
        return self

    @property
    def safe_uri(self) -> str:
        if not self.uri:
            return "<no configurada>"
        parts = urlsplit(self.uri)
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
