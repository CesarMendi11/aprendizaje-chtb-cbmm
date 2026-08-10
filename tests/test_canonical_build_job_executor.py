from __future__ import annotations

import json
import uuid
from pathlib import Path

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
    assert checkpoints[-1][0] == "exporting_canonical"
    assert checkpoints[-1][1]["progress_total"] == 4
