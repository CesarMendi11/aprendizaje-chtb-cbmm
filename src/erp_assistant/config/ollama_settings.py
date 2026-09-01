from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class OllamaConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class OllamaEmbeddingSettings:
    url: str = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_OLLAMA_URL", "http://127.0.0.1:11434")
    )
    model: str = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    )
    timeout: float = field(
        default_factory=lambda: float(os.getenv("ERP_ASSISTANT_OLLAMA_TIMEOUT", "30"))
    )
    batch_size: int = field(
        default_factory=lambda: int(os.getenv("ERP_ASSISTANT_EMBEDDING_BATCH_SIZE", "32"))
    )

    def __post_init__(self) -> None:
        url = str(self.url or "").strip().rstrip("/")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or any(char.isspace() for char in url)
        ):
            raise OllamaConfigurationError("ERP_ASSISTANT_OLLAMA_URL inválida")
        if self.batch_size <= 0:
            raise OllamaConfigurationError(
                "ERP_ASSISTANT_EMBEDDING_BATCH_SIZE debe ser mayor que cero"
            )
        object.__setattr__(self, "url", url)
