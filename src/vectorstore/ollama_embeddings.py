from __future__ import annotations

from typing import Any

import httpx

from src.config.ollama_settings import OllamaEmbeddingSettings


class OllamaEmbeddingError(RuntimeError):
    pass


class OllamaEmbeddingClient:
    def __init__(self, settings: OllamaEmbeddingSettings | None = None, *, client=None):
        self.settings = settings or OllamaEmbeddingSettings()
        self.client = client
        self.dimensions: int | None = None

    @property
    def model(self) -> str:
        return self.settings.model

    def embed(self, inputs: str | list[str]) -> list[list[float]]:
        values = [inputs] if isinstance(inputs, str) else list(inputs)
        if not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("Ollama requiere uno o más textos no vacíos")
        payload = {"model": self.model, "input": values}
        try:
            if self.client is not None:
                response = self.client.post("/api/embed", json=payload)
            else:
                response = httpx.post(
                    f"{self.settings.url}/api/embed",
                    json=payload,
                    timeout=self.settings.timeout,
                )
            response.raise_for_status()
            data: Any = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaEmbeddingError(
                f"No se pudieron obtener embeddings de Ollama ({self.settings.url})"
            ) from exc
        vectors = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(values) or not vectors:
            raise OllamaEmbeddingError("Ollama devolvió una cantidad inválida de embeddings")
        if not all(isinstance(vector, list) for vector in vectors):
            raise OllamaEmbeddingError("Ollama devolvió vectores con formato inválido")
        dimensions = {len(vector) for vector in vectors}
        if len(dimensions) != 1 or 0 in dimensions:
            raise OllamaEmbeddingError("Ollama devolvió embeddings vacíos o inconsistentes")
        if any(
            not all(isinstance(number, (int, float)) for number in vector) for vector in vectors
        ):
            raise OllamaEmbeddingError("Ollama devolvió valores de embedding inválidos")
        dimension = dimensions.pop()
        if self.dimensions is not None and dimension != self.dimensions:
            raise OllamaEmbeddingError(
                "La dimensionalidad de embeddings cambió durante la ejecución"
            )
        self.dimensions = dimension
        return [[float(number) for number in vector] for vector in vectors]
