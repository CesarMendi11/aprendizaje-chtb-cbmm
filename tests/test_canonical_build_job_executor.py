from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.knowledge.canonical.builder import CanonicalKnowledgeBuilder
from src.pipeline.canonical_build_job_executor import CanonicalBuildJobExecutor


def write_profile(path: Path, structural_dir: Path) -> None:
    path.write_text(
        f"""
erp:
  name: Demo ERP
  code: demo
  base_url: http://erp.test
login:
  url: http://erp.test/login
  username_selector: '#user'
  password_selector: '#password'
  submit_selector: '#submit'
  success_url_contains: /admin
navigation:
  home_url: /admin/home
exploration:
  allowed_routes: [/admin]
  blocked_routes: []
safety:
  default_decision: deny
extraction: {{}}
output:
  raw_playwright_dir: data/raw/playwright
  html_dir: data/raw/html
  screenshots_dir: data/raw/screenshots
  processed_structural_dir: {structural_dir.as_posix()}
  review_structural_dir: data/review/structural
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_canonical_build_job_uses_isolated_crawl_artifacts(tmp_path):
    source_id = uuid.uuid4()
    runs_root = tmp_path / "runs"
    structural = runs_root / str(source_id) / "processed" / "structural"
    structural.mkdir(parents=True)
    (structural / "screen_index.json").write_text(
        json.dumps(
            {
                "screens": [
                    {
                        "route": "/admin/cuentasxcobrar/retenciones",
                        "functional_title": "Retenciones",
                        "inputs": [{"label": "RUC", "name": "ruc"}],
                        "buttons": [{"text": "Buscar"}],
                        "tables": [{"headers": ["RUC", "Número"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yaml"
    write_profile(profile, structural)
    checkpoints = []

    result = CanonicalBuildJobExecutor(
        profile_path=profile,
        runs_root=runs_root,
    ).execute(
        job_id=uuid.uuid4(),
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        parameters={"source_crawl_job_id": str(source_id)},
        progress=lambda stage, payload: checkpoints.append((stage, payload)),
    )

    canonical = runs_root / str(source_id) / "processed" / "canonical"
    assert (canonical / "knowledge.json").is_file()
    assert (canonical / "manifest.json").is_file()
    assert (canonical / "build_report.json").is_file()
    assert result["source_crawl_job_id"] == str(source_id)
    assert result["statistics"]["screens"] == 1
    assert result["statistics"]["fields"] == 1
    assert result["statistics"]["controls"] == 1
    assert result["validation_errors"] == 0
    assert result["snapshot_mode"] == "partial"
    assert result["snapshot_scope"] == "screen"
    assert result["snapshot_target"] == "/admin/cuentasxcobrar/retenciones"
    manifest = json.loads((canonical / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot"] == {
        "mode": "partial",
        "scope": "screen",
        "target": "/admin/cuentasxcobrar/retenciones",
        "target_module_id": None,
        "base_knowledge_version_id": None,
        "base_knowledge_version": None,
        "erp_id": None,
    }
    assert checkpoints[-1][0] == "exporting_canonical"
    assert checkpoints[-1][1]["progress_total"] == 4


def test_canonical_build_marks_module_snapshot_with_pinned_base(tmp_path):
    source_id = uuid.uuid4()
    base_version_id = uuid.uuid4()
    runs_root = tmp_path / "runs"
    structural = runs_root / str(source_id) / "processed" / "structural"
    structural.mkdir(parents=True)
    artifacts = {
        "screen_index.json": {
            "screens": [
                {
                    "route": "/admin/tracking",
                    "functional_title": "Tracking",
                }
            ]
        },
        "routes_graph.json": {
            "nodes": [
                {"id": "/admin/home", "route": "/admin/home"},
                {
                    "id": "/admin/home#state:tracking",
                    "route": "/admin/home#state:tracking",
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/admin/home",
                        "path": {
                            "depth": 1,
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Tracking",
                                        "selector": "#tracking",
                                    }
                                }
                            ],
                        },
                    },
                },
                {"id": "/admin/tracking", "route": "/admin/tracking"},
            ],
            "edges": [
                {
                    "source": "/admin/home",
                    "target": "/admin/home#state:tracking",
                    "label": "Tracking",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "#tracking",
                    },
                },
                {
                    "source": "/admin/home#state:tracking",
                    "target": "/admin/tracking",
                    "label": "Tracking",
                    "kind": "ui_event_discovered_href",
                    "metadata": {},
                },
            ],
        },
    }
    for name, payload in artifacts.items():
        (structural / name).write_text(json.dumps(payload), encoding="utf-8")

    profile_dict = {
        "erp": {
            "name": "Demo ERP",
            "code": "demo",
            "base_url": "http://erp.test",
        },
        "navigation": {"home_url": "/admin/home"},
    }
    preview = CanonicalKnowledgeBuilder().build(profile_dict, artifacts)
    target_module = next(module for module in preview.modules if module.name == "Tracking")

    profile = tmp_path / "profile.yaml"
    write_profile(profile, structural)
    result = CanonicalBuildJobExecutor(
        profile_path=profile,
        runs_root=runs_root,
    ).execute(
        job_id=uuid.uuid4(),
        scope="module",
        target=target_module.id,
        parameters={
            "source_crawl_job_id": str(source_id),
            "target_module_id": target_module.id,
            "base_knowledge_version_id": str(base_version_id),
            "base_knowledge_version": "active-v10",
            "erp_id": preview.erp_system.id,
        },
    )

    assert result["snapshot_mode"] == "partial"
    assert result["snapshot_scope"] == "module"
    assert result["target_module_id"] == target_module.id
    assert result["base_knowledge_version_id"] == str(base_version_id)
    assert result["base_knowledge_version"] == "active-v10"

    manifest = json.loads(
        (runs_root / str(source_id) / "processed" / "canonical" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["snapshot"]["mode"] == "partial"
    assert manifest["snapshot"]["scope"] == "module"
    assert manifest["snapshot"]["target_module_id"] == target_module.id
    assert manifest["snapshot"]["base_knowledge_version_id"] == str(base_version_id)
    assert manifest["snapshot"]["base_knowledge_version"] == "active-v10"
    assert manifest["snapshot"]["erp_id"] == preview.erp_system.id
