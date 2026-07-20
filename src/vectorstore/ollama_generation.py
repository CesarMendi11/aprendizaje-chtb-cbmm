from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OllamaGenerationSettings:
    url: str = os.getenv("ERP_ASSISTANT_OLLAMA_URL", "http://127.0.0.1:11434")
    model: str = os.getenv("ERP_ASSISTANT_GENERATION_MODEL", "llama3.2:3b")
    timeout: float = float(os.getenv("ERP_ASSISTANT_OLLAMA_TIMEOUT", "30"))


class OllamaGenerationError(RuntimeError):
    pass


class OllamaGenerationClient:
    def __init__(self, settings=None, *, client=None):
        self.settings = settings or OllamaGenerationSettings()
        self.client = client

    def generate(self, prompt: str, *, system: str) -> str:
        if not prompt.strip():
            raise ValueError("El prompt de Ollama no puede estar vacío")
        payload = {
            "model": self.settings.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            if self.client is not None:
                response = self.client.post("/api/generate", json=payload)
            else:
                response = httpx.post(
                    f"{self.settings.url.rstrip('/')}/api/generate",
                    json=payload,
                    timeout=self.settings.timeout,
                )
            response.raise_for_status()
            data: Any = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaGenerationError(
                f"No se pudo generar respuesta con Ollama ({self.settings.url})"
            ) from exc
        answer = data.get("response") if isinstance(data, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise OllamaGenerationError("Ollama devolvió una respuesta vacía")
        return answer.strip()
