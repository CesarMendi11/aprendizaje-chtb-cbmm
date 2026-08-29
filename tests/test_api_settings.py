from pathlib import Path

from src.config.api_settings import ApiSettings, PROJECT_ROOT


def test_api_settings_centralize_runtime_environment(monkeypatch):
    monkeypatch.setenv("ERP_ASSISTANT_HYBRID_API", "1")
    monkeypatch.setenv("API_RELOAD", "1")
    monkeypatch.setenv("ERP_ASSISTANT_CRAWL_PROFILE", "configs/custom.yaml")

    settings = ApiSettings()

    assert settings.hybrid_api_enabled is True
    assert settings.reload is True
    assert settings.crawl_profile_path == PROJECT_ROOT / Path("configs/custom.yaml")
