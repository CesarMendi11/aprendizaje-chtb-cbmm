import pytest

from src.config.ollama_settings import (
    OllamaConfigurationError,
    OllamaEmbeddingSettings,
)


def test_ollama_settings_normalize_trailing_slash():
    settings = OllamaEmbeddingSettings(url="http://127.0.0.1:11434/")
    assert settings.url == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "url",
    [
        "http:// :11434",
        "127.0.0.1:11434",
        "ftp://127.0.0.1:11434",
        "",
    ],
)
def test_ollama_settings_reject_malformed_urls(url):
    with pytest.raises(OllamaConfigurationError, match="OLLAMA_URL inválida"):
        OllamaEmbeddingSettings(url=url)
