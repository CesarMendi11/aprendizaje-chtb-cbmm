from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from src.knowledge.canonical.builder import CanonicalKnowledgeBuilder
from src.knowledge.canonical.ids import stable_id
from src.pipeline.canonical_build_job_executor import (
    CanonicalBuildJobExecutionError,
    CanonicalBuildJobExecutor,
)
from tests.fixtures.crawl_quality import (
    certified_crawl_quality,
    source_crawl_result,
    write_state_exploration_summary,
)


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


def pinned_source_crawl_result(profile_path: Path, source_id, *, scope, target):
    return {
        **source_crawl_result(source_id, scope=scope, target=target),
        "profile_path": str(profile_path),
        "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
    }


def test_canonical_build_job_uses_isolated_crawl_artifacts(tmp_path):
    source_id = uuid.uuid4()
    runs_root = tmp_path / "runs"
    structural = runs_root / str(source_id) / "processed" / "structural"
    structural.mkdir(parents=True)
    write_state_exploration_summary(structural)
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
    (structural / "network_evidence.json").write_text(
        json.dumps(
            {
                "capture_policy": {
                    "bodies_captured": False,
                    "headers_captured": False,
                    "query_values_captured": False,
                    "resource_types": ["xhr"],
                },
                "observations": [
                    {
                        "screen_route": "/admin/cuentasxcobrar/retenciones",
                        "method": "GET",
                        "endpoint_path": "/api/retenciones/{id}",
                        "origin_id": "same_origin",
                        "origin_kind": "same_origin",
                        "resource_type": "xhr",
                        "query_keys": ["page"],
                        "status_codes": [200],
                        "observed_count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    review = runs_root / str(source_id) / "review" / "structural"
    review.mkdir(parents=True)
    (review / "screen_ui_events_state_20260819_010000_uncertainty.json").write_text(
        json.dumps(
            {
                "route": "/admin/cuentasxcobrar/retenciones",
                "results": [
                    {
                        "candidate": {"event_category": "open_dropdown"},
                        "changed": False,
                        "error": "timeout: overlay intercepts pointer events",
                    }
                ],
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
        parameters={
            "source_crawl_job_id": str(source_id),
            "source_crawl_result": pinned_source_crawl_result(
                profile,
                source_id,
                scope="screen",
                target="/admin/cuentasxcobrar/retenciones",
            ),
            "target_screen_id": stable_id(
                "screen",
                stable_id("erp", "demo"),
                "/admin/cuentasxcobrar/retenciones",
            ),
            "base_knowledge_version_id": str(uuid.uuid4()),
            "base_knowledge_version": "active-v1",
            "erp_id": stable_id("erp", "demo"),
        },
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
    assert result["network_evidence"] == 2
    assert result["network_evidence_screens"] == 1
    assert result["snapshot_mode"] == "partial"
    assert result["snapshot_scope"] == "screen"
    assert result["snapshot_target"] == "/admin/cuentasxcobrar/retenciones"
    assert result["crawl_execution_quality"] == certified_crawl_quality(
        run_id=source_id,
        scope="screen",
        target="/admin/cuentasxcobrar/retenciones",
        execution_evidence_present=True,
        ui_event_result_files=1,
        events_evaluated=1,
        other_error_events=1,
    )
    knowledge = json.loads((canonical / "knowledge.json").read_text(encoding="utf-8"))
    network = [
        item
        for item in knowledge["evidence"]
        if item["evidence_type"] == "network_trace"
    ]
    assert len(network) == 1
    assert network[0]["metadata"]["bodies_captured"] is False
    assert network[0]["metadata"]["headers_captured"] is False
    report = json.loads((canonical / "build_report.json").read_text(encoding="utf-8"))
    assert report["crawl_execution_quality"] == result["crawl_execution_quality"]
    manifest = json.loads((canonical / "manifest.json").read_text(encoding="utf-8"))
    profile_ref = f"profile:{profile}"
    assert result["profile_path"] == str(profile)
    assert result["profile_sha256"] == hashlib.sha256(profile.read_bytes()).hexdigest()
    assert knowledge["source_profile"] == str(profile)
    assert knowledge["source_artifact_hashes"][profile_ref] == result["profile_sha256"]
    assert manifest["source_artifact_hashes"][profile_ref] == result["profile_sha256"]
    assert manifest["crawl_execution_quality"] == result["crawl_execution_quality"]
    assert manifest["snapshot"] == {
        "mode": "partial",
        "scope": "screen",
        "target": "/admin/cuentasxcobrar/retenciones",
        "target_module_id": None,
        "target_screen_id": stable_id(
            "screen", stable_id("erp", "demo"), "/admin/cuentasxcobrar/retenciones"
        ),
        "base_knowledge_version_id": result["base_knowledge_version_id"],
        "base_knowledge_version": "active-v1",
        "erp_id": stable_id("erp", "demo"),
    }
    assert checkpoints[-1][0] == "exporting_canonical"
    assert checkpoints[-1][1]["progress_total"] == 4


@pytest.mark.parametrize("scope", ["full", "module", "screen"])
def test_canonical_build_blocks_source_with_state_restore_failure(tmp_path, scope):
    source_id = uuid.uuid4()
    runs_root = tmp_path / "runs"
    structural = runs_root / str(source_id) / "processed" / "structural"
    structural.mkdir(parents=True)
    write_state_exploration_summary(structural)
    (structural / "screen_index.json").write_text(
        json.dumps({"screens": []}),
        encoding="utf-8",
    )

    review = runs_root / str(source_id) / "review" / "structural"
    review.mkdir(parents=True)
    (review / "home_ui_events_state_20260819_010000_uncertainty.json").write_text(
        json.dumps(
            {
                "route": "/admin/home",
                "results": [
                    {
                        "candidate": {
                            "event_category": "expand_menu",
                            "label": "Menu",
                        },
                        "changed": False,
                        "interaction_succeeded": False,
                        "error": "state_restore_failed",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    target = None
    if scope == "module":
        target = "module:test"
    elif scope == "screen":
        target = "/admin/test"

    with pytest.raises(
        CanonicalBuildJobExecutionError,
        match="state_restore_failures=1",
    ):
        CanonicalBuildJobExecutor(
            profile_path=tmp_path / "profile.yaml",
            runs_root=runs_root,
        ).execute(
            job_id=uuid.uuid4(),
            scope=scope,
            target=target,
            parameters={
                "source_crawl_job_id": str(source_id),
                "source_crawl_result": {
                    **source_crawl_result(source_id, scope=scope, target=target),
                    "profile_path": str(tmp_path / "profile.yaml"),
                    "profile_sha256": "a" * 64,
                },
            },
        )

    assert not (
        runs_root / str(source_id) / "processed" / "canonical"
    ).exists()


def test_canonical_build_marks_module_snapshot_with_pinned_base(tmp_path):
    source_id = uuid.uuid4()
    base_version_id = uuid.uuid4()
    runs_root = tmp_path / "runs"
    structural = runs_root / str(source_id) / "processed" / "structural"
    structural.mkdir(parents=True)
    write_state_exploration_summary(structural)
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
            "source_crawl_result": pinned_source_crawl_result(
                profile,
                source_id,
                scope="module",
                target=target_module.id,
            ),
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


def test_canonical_build_rejects_missing_profile_provenance(tmp_path):
    executor = CanonicalBuildJobExecutor(
        profile_path=tmp_path / "profile.yaml",
        runs_root=tmp_path / "runs",
    )

    with pytest.raises(
        CanonicalBuildJobExecutionError,
        match="profile_path/profile_sha256",
    ):
        executor.execute(
            job_id=uuid.uuid4(),
            scope="full",
            target=None,
            parameters={
                "source_crawl_job_id": str(uuid.uuid4()),
                "source_crawl_result": {},
            },
        )


def test_canonical_build_rejects_profile_hash_mismatch_before_consuming_profile(tmp_path):
    profile = tmp_path / "profile.yaml"
    structural = tmp_path / "structural"
    structural.mkdir()
    write_profile(profile, structural)
    executor = CanonicalBuildJobExecutor(
        profile_path=profile,
        runs_root=tmp_path / "runs",
    )

    with pytest.raises(
        CanonicalBuildJobExecutionError,
        match="cambió desde el crawl fuente",
    ):
        executor._load_pinned_profile(str(profile), "0" * 64)
