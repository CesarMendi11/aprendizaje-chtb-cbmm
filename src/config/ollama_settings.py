from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OllamaEmbeddingSettings:
    url: str = field(
        default_factory=lambda: os.getenv(
            "ERP_ASSISTANT_OLLAMA_URL", "http://127.0.0.1:11434"
        ).rstrip("/")
    )
    model: str = field(
        default_factory=lambda: os.getenv("ERP_ASSISTANT_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
    )
    timeout: float = field(
        default_factory=lambda: float(os.getenv("ERP_ASSISTANT_OLLAMA_TIMEOUT", "30"))
    )
