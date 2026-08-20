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

        if self.client is not None:
            return self._embed_batches(values, client=self.client)

        with httpx.Client(
            base_url=self.settings.url,
            timeout=self.settings.timeout,
        ) as client:
            return self._embed_batches(values, client=client)

    def _embed_batches(self, values: list[str], *, client) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch_size = self.settings.batch_size
        total_batches = (len(values) + batch_size - 1) // batch_size
        for batch_number, start in enumerate(range(0, len(values), batch_size), start=1):
            batch = values[start : start + batch_size]
            vectors.extend(
                self._embed_batch(
                    batch,
                    client=client,
                    batch_number=batch_number,
                    total_batches=total_batches,
                )
            )
        return vectors

    def _embed_batch(
        self,
        values: list[str],
        *,
        client,
        batch_number: int,
        total_batches: int,
    ) -> list[list[float]]:
        payload = {"model": self.model, "input": values}
        try:
            response = client.post("/api/embed", json=payload)
            response.raise_for_status()
            data: Any = response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise OllamaEmbeddingError(
                "No se pudieron obtener embeddings de Ollama "
                f"(lote {batch_number}/{total_batches})"
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
