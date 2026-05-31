from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def safe_slug(value: str, fallback: str = "artifact") -> str:
    """
    Convierte textos o rutas en nombres seguros de archivo.

    Ejemplo:
    /admin/cuentas por cobrar/facturas
    ->
    admin_cuentas_por_cobrar_facturas
    """

    value = value.strip().lower()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")

    return value or fallback


class ArtifactStorage:
    """
    Maneja el guardado de artefactos del crawler.

    Responsabilidad:
    - Crear carpetas necesarias.
    - Guardar JSON crudo.
    - Guardar HTML.
    - Guardar rutas estructurales procesadas.
    - Guardar archivos de incertidumbre para revisión.
    """

    def __init__(self, profile: dict[str, Any]):
        output = profile.get("output", {})

        self.raw_playwright_dir = Path(output["raw_playwright_dir"])
        self.html_dir = Path(output["html_dir"])
        self.screenshots_dir = Path(output["screenshots_dir"])
        self.marked_screenshots_dir = Path(
            output.get("marked_screenshots_dir", "data/raw/marked_screenshots")
        )

        self.processed_structural_dir = Path(output["processed_structural_dir"])
        self.processed_semantic_dir = Path(
            output.get("processed_semantic_dir", "data/processed/semantic")
        )

        self.review_structural_dir = Path(output["review_structural_dir"])
        self.review_semantic_dir = Path(
            output.get("review_semantic_dir", "data/review/semantic")
        )

        self.approved_neo4j_dir = Path(
            output.get("approved_neo4j_dir", "data/approved/neo4j")
        )
        self.approved_chromadb_dir = Path(
            output.get("approved_chromadb_dir", "data/approved/chromadb")
        )

        self.rejected_dir = Path(output.get("rejected_dir", "data/rejected"))
        self.cache_dir = Path(output.get("cache_dir", "data/cache"))

        self.ensure_directories()

    def ensure_directories(self) -> None:
        directories = [
            self.raw_playwright_dir,
            self.html_dir,
            self.screenshots_dir,
            self.marked_screenshots_dir,
            self.processed_structural_dir,
            self.processed_semantic_dir,
            self.review_structural_dir,
            self.review_semantic_dir,
            self.approved_neo4j_dir,
            self.approved_chromadb_dir,
            self.rejected_dir,
            self.cache_dir,
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def save_json(self, data: dict[str, Any], directory: Path, filename: str) -> Path:
        path = directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

        return path

    def save_raw_screen_json(self, data: dict[str, Any], prefix: str) -> Path:
        filename = f"{safe_slug(prefix)}.json"
        return self.save_json(data, self.raw_playwright_dir, filename)

    def save_processed_structural_json(
        self, data: dict[str, Any], filename: str
    ) -> Path:
        return self.save_json(data, self.processed_structural_dir, filename)

    def save_uncertainty_json(self, data: dict[str, Any], prefix: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_slug(prefix)}_{timestamp}_uncertainty.json"

        return self.save_json(data, self.review_structural_dir, filename)

    def save_html_content(self, html: str, prefix: str) -> Path:
        filename = f"{safe_slug(prefix)}.html"
        path = self.html_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            file.write(html)

        return path

    def save_screenshot_bytes(self, content: bytes, prefix: str) -> Path:
        filename = f"{safe_slug(prefix)}.png"
        path = self.screenshots_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("wb") as file:
            file.write(content)

        return path