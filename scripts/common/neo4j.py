from __future__ import annotations

from erp_assistant.config.neo4j_settings import Neo4jSettings
from erp_assistant.projections.neo4j.client import Neo4jClient
from erp_assistant.projections.neo4j.repository import Neo4jRepository
from erp_assistant.structural.canonical.privacy import sanitize_text


def neo4j_client(settings: Neo4jSettings | None = None):
    return Neo4jClient(settings or Neo4jSettings())


def neo4j_repository(settings: Neo4jSettings | None = None):
    return Neo4jRepository(neo4j_client(settings))


def safe_neo4j_error(exc: Exception, settings: Neo4jSettings) -> str:
    message = str(exc)
    if settings.password:
        message = message.replace(settings.password, "[redacted]")
    clean, _ = sanitize_text(message, 400)
    return clean or "Error Neo4j sanitizado"
