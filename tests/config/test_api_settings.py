from pathlib import Path

from src.config.api_settings import ApiSettings
from src.config.pipeline_settings import PipelineSettings


def test_api_settings_centralize_runtime_environment(monkeypatch):
    monkeypatch.setenv("API_RELOAD", "1")
    settings = ApiSettings()

    assert settings.reload is True


def test_pipeline_settings_centralize_pipeline_environment(monkeypatch):
    monkeypatch.setenv("ERP_ASSISTANT_CRAWL_PROFILE", "configs/custom.yaml")
    monkeypatch.setenv("ERP_ASSISTANT_PIPELINE_RUNS_DIR", "data/runs/custom")

    settings = PipelineSettings()

    assert settings.crawl_profile_path == Path("configs/custom.yaml")
    assert settings.runs_root == Path("data/runs/custom")
