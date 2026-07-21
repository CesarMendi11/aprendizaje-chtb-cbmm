from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import httpx

from src.vectorstore.ollama_generation import OllamaGenerationSettings

from .errors import (
    EmptyStructuredOutputError,
    OllamaBodyError,
    OllamaHTTPError,
    OllamaResponseTooLargeError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    StructuredModeUnsupportedError,
)

MAX_HTTP_BODY_BYTES = 256_000
MAX_GENERATED_TEXT_BYTES = 64_000


@dataclass(frozen=True)
class StructuredGenerationResponse:
    text: str
    mode: Literal["json_schema", "json"]


class OllamaStructuredGenerationClient:
    def __init__(
        self, settings=None, *, client=None, mode: str = "json_schema", timeout: float = 120
    ):
        if mode not in {"json_schema", "json"}:
            raise ValueError("Modo estructurado no soportado")
        self.settings = settings or OllamaGenerationSettings()
        self.client = client
        self.mode = mode
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("El timeout estructurado debe ser positivo")

    def generate(
        self, prompt: str, *, system: str, schema: dict[str, Any]
    ) -> StructuredGenerationResponse:
        if not prompt.strip() or not system.strip():
            raise ValueError("Prompt estructurado vacío")
        payload = {
            "model": self.settings.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "format": schema if self.mode == "json_schema" else "json",
            "options": {"temperature": 0, "num_predict": 1024},
        }
        try:
            response = (
                self.client.post("/api/generate", json=payload, timeout=self.timeout)
                if self.client is not None
                else httpx.post(
                    f"{self.settings.url.rstrip('/')}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                )
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError("Ollama excedió el tiempo de espera") from exc
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError("Ollama no está disponible") from exc
        except httpx.HTTPStatusError as exc:
            if self.mode == "json_schema" and exc.response.status_code in {400, 404, 422}:
                raise StructuredModeUnsupportedError("Ollama rechazó el modo JSON Schema") from exc
            raise OllamaHTTPError("Ollama devolvió un error HTTP") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError("No fue posible contactar Ollama") from exc
        body = getattr(response, "content", b"")
        if isinstance(body, bytes) and len(body) > MAX_HTTP_BODY_BYTES:
            raise OllamaResponseTooLargeError("La respuesta de Ollama excede el límite")
        content_type = str(getattr(response, "headers", {}).get("content-type", ""))
        if content_type and "json" not in content_type.casefold():
            raise OllamaBodyError("Ollama devolvió un Content-Type inválido")
        try:
            data: Any = response.json()
        except (ValueError, TypeError) as exc:
            raise OllamaBodyError("Ollama devolvió un cuerpo inválido") from exc
        answer = data.get("response") if isinstance(data, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            raise EmptyStructuredOutputError("Ollama devolvió una salida vacía")
        if len(answer.encode("utf-8")) > MAX_GENERATED_TEXT_BYTES:
            raise OllamaResponseTooLargeError("La salida generada excede el límite")
        return StructuredGenerationResponse(answer.strip(), self.mode)
