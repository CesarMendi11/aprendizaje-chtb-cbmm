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



def test_route_diagnostic_separates_route_detection_from_title_mismatch(tmp_path):
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
                "name": "Etiqueta distinta",
                "route": "/admin/general/anios",
                "notes": "fixture intencional",
            },
        ],
    )

    _write_knowledge(
        knowledge_path
    )

    payload = evaluate(
        reference_path,
        knowledge_path,
    )

    strict_screen = payload[
        "metrics"
    ][
        "screen"
    ]

    assert strict_screen[
        "tp"
    ] == 0

    assert strict_screen[
        "fp"
    ] == 1

    assert strict_screen[
        "fn"
    ] == 1

    diagnostics = payload[
        "diagnostics"
    ][
        "screen_identity"
    ]

    route_metric = diagnostics[
        "screen_route"
    ]

    assert route_metric[
        "tp"
    ] == 1

    assert route_metric[
        "fp"
    ] == 0

    assert route_metric[
        "fn"
    ] == 0

    assert diagnostics[
        "title_match_on_shared_routes"
    ][
        "accuracy"
    ] == 0.0

    assert diagnostics[
        "hierarchy_match_on_shared_routes"
    ][
        "accuracy"
    ] == 1.0



def test_reference_allows_confirmed_absent_screen_title(tmp_path):
    from scripts.experiments.evaluate_structural import load_reference

    reference_path = tmp_path / "reference.csv"
    reference_path.write_text(
        "entity_type,parent_module_path,name,route,notes\n"
        'screen,General,,/admin/general/blank,"title_status: absent; '
        'menu_label: Blank"\n',
        encoding="utf-8",
    )

    items = load_reference(reference_path)

    assert len(items) == 1
    assert items[0].entity_type == "screen"
    assert items[0].name == ""
    assert items[0].route == "/admin/general/blank"


def test_reference_rejects_blank_screen_without_absent_title_marker(tmp_path):
    import pytest

    from scripts.experiments.evaluate_structural import load_reference

    reference_path = tmp_path / "reference.csv"
    reference_path.write_text(
        "entity_type,parent_module_path,name,route,notes\n"
        "screen,General,,/admin/general/blank,menu_label: Blank\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="title_status: absent",
    ):
        load_reference(reference_path)


def test_reference_rejects_blank_module_even_with_absent_title_marker(tmp_path):
    import pytest

    from scripts.experiments.evaluate_structural import load_reference

    reference_path = tmp_path / "reference.csv"
    reference_path.write_text(
        "entity_type,parent_module_path,name,route,notes\n"
        'module,,,,"title_status: absent"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="name es obligatorio para module",
    ):
        load_reference(reference_path)


def test_reference_rejects_absent_title_marker_with_nonempty_screen_name(tmp_path):
    import pytest

    from scripts.experiments.evaluate_structural import load_reference

    reference_path = tmp_path / "reference.csv"
    reference_path.write_text(
        "entity_type,parent_module_path,name,route,notes\n"
        'screen,General,Titulo,/admin/general/x,"title_status: absent"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="incompatible",
    ):
        load_reference(reference_path)


def test_policy_scope_excludes_blocked_screens_and_modules_from_primary_metrics(
    tmp_path,
):
    reference_path = tmp_path / "reference.csv"
    knowledge_path = tmp_path / "knowledge.json"
    policy_path = tmp_path / "policy.json"

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
                "route": "/admin/general/anios",
                "notes": "",
            },
            {
                "entity_type": "module",
                "parent_module_path": "",
                "name": "Seguridad",
                "route": "",
                "notes": "",
            },
            {
                "entity_type": "screen",
                "parent_module_path": "Seguridad",
                "name": "Usuarios",
                "route": "/admin/seguridad/usuarios",
                "notes": "",
            },
        ],
    )
    _write_knowledge(knowledge_path)
    policy_path.write_text(
        json.dumps(
            {
                "contract_id": "policy-test",
                "status": "FROZEN_PRE_FORMAL002_INPUT",
                "policy_source": {
                    "blocked_route_prefix": "/admin/seguridad",
                },
                "policy_blocked_routes": [
                    "/admin/seguridad/usuarios",
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = evaluate(
        reference_path,
        knowledge_path,
        policy_path,
    )

    assert payload["metrics"]["module"]["reference"] == 2
    assert payload["metrics"]["module"]["fn"] == 0
    assert payload["metrics"]["screen"]["reference"] == 1
    assert payload["metrics"]["screen"]["fn"] == 0
    assert payload["metrics"]["screen_hierarchy"]["reference"] == 1
    assert payload["metrics"]["screen_hierarchy"]["fn"] == 0

    policy = payload["policy_scope"]
    assert policy["policy_blocked_screen_count"] == 1
    assert policy["policy_blocked_module_paths"] == ["seguridad"]
    assert policy["eligible_screen_count"] == 1
    assert policy["policy_violation_count"] == 0

    all_routes = payload["diagnostics"]["all_reference_screen_route"]
    assert all_routes["reference"] == 2
    assert all_routes["detected"] == 1
    assert all_routes["fn"] == 1


def test_rq1_screen_identity_uses_observed_in_screen_title_not_functional_title(tmp_path):
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
                "name": "",
                "route": "/admin/general/anios",
                "notes": "title_status: absent; menu_label: Año",
            },
        ],
    )

    erp = ERPSystem(id="erp:1", slug="cbmm", name="ERP", profile_name="cbmm")
    general = Module(
        id="module:general", erp_id=erp.id, parent_module_id=None, depth=0,
        navigation_path=["General"], name="General", normalized_name="general",
    )
    catalogos = Module(
        id="module:catalogos", erp_id=erp.id, parent_module_id=general.id, depth=1,
        navigation_path=["General", "Catálogos"], name="Catálogos",
        normalized_name="catalogos",
    )
    screen = Screen(
        id="screen:anio", erp_id=erp.id, module_id=catalogos.id,
        title="Año", normalized_title="ano", route="/admin/general/anios",
        metadata={"in_screen_title": "", "in_screen_title_source": "not_observed"},
    )
    knowledge = CanonicalKnowledgeBase(
        schema_version="1.1.0", knowledge_version="fixture-v1",
        generated_at=datetime.now(timezone.utc), source_profile="cbmm",
        source_artifacts=[], source_artifact_hashes={}, erp_system=erp,
        modules=[general, catalogos], screens=[screen],
        statistics={"modules": 2, "screens": 1},
    )
    knowledge_path.write_text(
        json.dumps(knowledge.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    payload = evaluate(reference_path, knowledge_path)
    assert payload["metrics"]["screen"]["tp"] == 1
    assert payload["metrics"]["screen_hierarchy"]["tp"] == 1
    assert payload["diagnostics"]["screen_identity"]["title_match_on_shared_routes"]["matches"] == 1
