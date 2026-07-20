import pytest

from src.config.database_settings import DatabaseConfigurationError, DatabaseSettings


def test_database_url_is_optional_for_api(monkeypatch):
    monkeypatch.delenv("ERP_ASSISTANT_DATABASE_URL", raising=False)
    settings = DatabaseSettings()
    assert settings.url is None
    with pytest.raises(DatabaseConfigurationError):
        settings.require_url()


def test_database_url_is_sanitized():
    settings = DatabaseSettings(url="postgresql+psycopg://user:secret@localhost:5433/db")
    assert "secret" not in settings.safe_url
    assert settings.safe_url == "postgresql+psycopg://user:***@localhost:5433/db"


def test_real_database_operations_require_postgresql():
    with pytest.raises(DatabaseConfigurationError):
        DatabaseSettings(url="sqlite:///unsafe.db").require_url()

