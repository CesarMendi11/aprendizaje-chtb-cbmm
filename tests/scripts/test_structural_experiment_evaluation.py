from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from erp_assistant.structural.canonical.models import (
    CanonicalKnowledgeBase,
    ERPSystem,
    Module,
    Screen,
)
from scripts.experiments.evaluate_structural import evaluate, load_reference


def _write_reference(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["entity_type", "parent_module_path", "name", "route", "notes"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_knowledge(path: Path) -> None:
    erp = ERPSystem(
        id="erp:1",
        slug="cbmm",
        name="ERP",
        profile_name="cbmm",
    )
    general = Module(
        id="module:general",
        erp_id=erp.id,
        parent_module_id=None,
        depth=0,
        navigation_path=["General"],
        name="General",
        normalized_name="general",
    )
    catalogos = Module(
        id="module:catalogos",
        erp_id=erp.id,
        parent_module_id=general.id,
        depth=1,
        navigation_path=["General", "Catálogos"],
        name="Catálogos",
        normalized_name="catalogos",
    )
    screen = Screen(
        id="screen:anio",
        erp_id=erp.id,
        module_id=catalogos.id,
        title="Año",
        normalized_title="ano",
        route="/admin/general/anios",
    )
    knowledge = CanonicalKnowledgeBase(
        schema_version="1.1.0",
        knowledge_version="fixture-v1",
        generated_at=datetime.now(timezone.utc),
        source_profile="cbmm",
        source_artifacts=[],
        source_artifact_hashes={},
        erp_system=erp,
        modules=[general, catalogos],
        screens=[screen],
        statistics={"modules": 2, "screens": 1},
    )
    path.write_text(
        json.dumps(knowledge.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_load_reference_rejects_empty_gold_standard(tmp_path):
    path = tmp_path / "reference.csv"
    _write_reference(path, [])

    with pytest.raises(ValueError, match="está vacío"):
        load_reference(path)


def test_evaluate_structural_census_reports_tp_fp_fn(tmp_path):
    reference_path = tmp_path / "reference.csv"
    knowledge_path = tmp_path / "knowledge.json"
    _write_reference(
        reference_path,
        [
            {
                "entity_type": "module",
                "parent_module_path": "",
                "name": "General",
                "route": "",
                "notes": "",
            },
            {
                "entity_type": "module",
                "parent_module_path": "General",
                "name": "Catálogos",
                "route": "",
                "notes": "",
            },
            {
                "entity_type": "screen",
                "parent_module_path": "General > Catálogos",
                "name": "Año",
                "route": "/admin/general/anios/",
                "notes": "",
            },
            {
                "entity_type": "screen",
                "parent_module_path": "General > Catálogos",
                "name": "Mes",
                "route": "/admin/general/meses",
                "notes": "esperada pero ausente en fixture",
            },
        ],
    )
    _write_knowledge(knowledge_path)

    payload = evaluate(reference_path, knowledge_path)

    assert payload["metrics"]["module"] == {
        "reference": 2,
        "detected": 2,
        "tp": 2,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "false_positives": [],
        "false_negatives": [],
    }
    screen = payload["metrics"]["screen"]
    assert screen["reference"] == 2
    assert screen["detected"] == 1
    assert screen["tp"] == 1
    assert screen["fp"] == 0
    assert screen["fn"] == 1
    assert screen["precision"] == 1.0
    assert screen["recall"] == 0.5
    assert round(screen["f1"], 6) == round(2 / 3, 6)
    assert screen["false_negatives"] == [{"route": "/admin/general/meses", "name": "mes"}]

    hierarchy = payload["metrics"]["screen_hierarchy"]
    assert hierarchy["tp"] == 1
    assert hierarchy["fn"] == 1
    assert hierarchy["false_negatives"] == [
        {
            "route": "/admin/general/meses",
            "module_path": "general > catalogos",
            "name": "mes",
        }
    ]
