from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx


class OllamaGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class OllamaGenerationSettings:
    url: str = field(
        default_factory=lambda: os.getenv(
            "ERP_ASSISTANT_OLLAMA_URL",
            "http://127.0.0.1:11434",
        )
    )
    model: str = field(
        default_factory=lambda: os.getenv(
            "ERP_ASSISTANT_GENERATION_MODEL",
            "llama3.2:3b",
        )
    )
    timeout: float = field(
        default_factory=lambda: float(
            os.getenv("ERP_ASSISTANT_OLLAMA_TIMEOUT", "30")
        )
    )
    structured_timeout: float = field(
        default_factory=lambda: float(
            os.getenv(
                "ERP_ASSISTANT_OLLAMA_STRUCTURED_TIMEOUT",
                "120",
            )
        )
    )

    def __post_init__(self) -> None:
        url = str(self.url or "").strip().rstrip("/")
        model = str(self.model or "").strip()
        parsed = urlsplit(url)

        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or any(char.isspace() for char in url)
        ):
            raise ValueError("ERP_ASSISTANT_OLLAMA_URL inválida")
        if not model:
            raise ValueError("ERP_ASSISTANT_GENERATION_MODEL no puede estar vacío")
        if self.timeout <= 0:
            raise ValueError("ERP_ASSISTANT_OLLAMA_TIMEOUT debe ser mayor que cero")
        if self.structured_timeout <= 0:
            raise ValueError(
                "ERP_ASSISTANT_OLLAMA_STRUCTURED_TIMEOUT debe ser mayor que cero"
            )

        object.__setattr__(self, "url", url)
        object.__setattr__(self, "model", model)


class OllamaGenerationClient:
    def __init__(
        self,
        settings: OllamaGenerationSettings | None = None,
        *,
        client=None,
    ):
        self.settings = settings or OllamaGenerationSettings()
        self.client = client

    def generate(self, prompt: str, *, system: str) -> str:
        prompt = str(prompt or "").strip()
        system = str(system or "").strip()
        if not prompt:
            raise ValueError("Ollama requiere un prompt no vacío")
        if not system:
            raise ValueError("Ollama requiere un system prompt no vacío")

        payload = {
            "model": self.settings.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            if self.client is not None:
                response = self.client.post(
                    "/api/generate",
                    json=payload,
                    timeout=self.settings.timeout,
                )
            else:
                response = httpx.post(
                    f"{self.settings.url}/api/generate",
                    json=payload,
                    timeout=self.settings.timeout,
                )
            response.raise_for_status()
            data: Any = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaGenerationError("No se pudo generar una respuesta con Ollama") from exc

        answer = data.get("response") if isinstance(data, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise OllamaGenerationError("Ollama devolvió una respuesta vacía o inválida")
        return answer.strip()
