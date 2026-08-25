from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.knowledge.canonical.privacy import sanitize_artifact_payload


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
    - Guardar JSON estructural sanitizado.
    - Guardar rutas estructurales procesadas.
    - Guardar archivos de incertidumbre sanitizados para revisión.
    - Bloquear persistencia durable de HTML y screenshots crudos.
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

        # Privacy contract: durable crawler evidence is structural JSON only.
        # Raw DOM and screenshots can contain row-level personal/financial data
        # that cannot be generically redacted with sufficient confidence.
        self.persist_html = False
        self.persist_screenshots = False

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

        safe_data = sanitize_artifact_payload(data)
        with path.open("w", encoding="utf-8") as file:
            json.dump(safe_data, file, ensure_ascii=False, indent=2)

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

    def save_html_content(self, html: str, prefix: str) -> Path | None:
        # Disabled by the privacy contract. Structural JSON remains the durable
        # evidence boundary; raw rendered DOM is intentionally not retained.
        return None

    def save_screenshot_bytes(self, content: bytes, prefix: str) -> Path | None:
        # Arbitrary screenshots cannot be reliably redacted for a generic ERP.
        # They are therefore excluded from durable crawler evidence.
        return None
