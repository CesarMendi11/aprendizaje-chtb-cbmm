from pathlib import Path

from erp_assistant.config.chroma_settings import ChromaSettings, PROJECT_ROOT


def test_chroma_settings_resolve_relative_environment_path(monkeypatch):
    monkeypatch.setenv("ERP_ASSISTANT_CHROMA_PATH", "data/chroma/custom")

    settings = ChromaSettings()

    assert settings.path == (PROJECT_ROOT / Path("data/chroma/custom")).resolve()


def test_chroma_settings_preserve_absolute_environment_path(monkeypatch, tmp_path):
    monkeypatch.setenv("ERP_ASSISTANT_CHROMA_PATH", str(tmp_path))

    settings = ChromaSettings()

    assert settings.path == tmp_path
