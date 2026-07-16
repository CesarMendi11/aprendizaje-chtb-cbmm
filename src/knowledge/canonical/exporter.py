from __future__ import annotations

import json
from pathlib import Path

from .manifest import create_manifest
from .models import CanonicalKnowledgeBase
from .validator import CanonicalKnowledgeValidator


class CanonicalKnowledgeExporter:
    def export(self, knowledge: CanonicalKnowledgeBase, output_dir: Path | str, *, pretty=True, build_report=None):
        issues = CanonicalKnowledgeValidator().validate(knowledge)
        errors = [item for item in issues if item.severity == "error"]
        if errors:
            raise ValueError(f"No se exporta conocimiento inválido: {len(errors)} errores")
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        payload = knowledge.model_dump(mode="json")
        kwargs = {"ensure_ascii": False, "sort_keys": True}
        if pretty: kwargs["indent"] = 2
        self._write(output / "knowledge.json", payload, kwargs)
        self._write(output / "manifest.json", create_manifest(knowledge, payload), kwargs)
        self._write(output / "build_report.json", build_report or {}, kwargs)
        return output

    @staticmethod
    def _write(path, payload, kwargs):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, **kwargs) + "\n", encoding="utf-8")
        temporary.replace(path)
