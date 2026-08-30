import pytest

from src.config.neo4j_settings import Neo4jConfigurationError, Neo4jSettings


def test_missing_neo4j_configuration_is_controlled(monkeypatch):
    for name in ("URI", "USER", "PASSWORD", "DATABASE"):
        monkeypatch.delenv(f"ERP_ASSISTANT_NEO4J_{name}", raising=False)
    settings = Neo4jSettings()
    with pytest.raises(Neo4jConfigurationError, match="Falta configuración"):
        settings.require()


def test_safe_uri_never_contains_credentials():
    settings = Neo4jSettings(
        uri="bolt://user:synthetic-secret@127.0.0.1:7687",
        user="user",
        password="synthetic-secret",
        database="neo4j",
    )
    assert settings.safe_uri == "bolt://127.0.0.1:7687"
    assert "synthetic-secret" not in settings.safe_uri
