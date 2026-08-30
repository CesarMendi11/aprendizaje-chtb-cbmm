from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from src.knowledge.crawl_execution_quality import CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION


def certified_crawl_quality(
    *,
    run_id: uuid.UUID | str | None = None,
    scope: str = "full",
    target: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    if run_id is None:
        run_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    payload = {
        "quality_contract_version": CRAWL_EXECUTION_QUALITY_CONTRACT_VERSION,
        "source_run_id": str(run_id),
        "source_scope": scope,
        "source_target": target,
        "execution_evidence_present": False,
        "ui_event_result_files": 0,
        "events_evaluated": 0,
        "ui_event_state_restore_failures": 0,
        "dynamic_state_restore_failures": 0,
        "state_restore_failures": 0,
        "dynamic_state_exploration_errors": 0,
        "navigation_errors": 0,
        "fixed_point_stalls": 0,
        "route_frontier_pending": 0,
        "state_frontier_pending": 0,
        "other_error_events": 0,
        "blocking_failures": 0,
        "gate_passed": True,
    }
    payload.update(overrides)
    return payload


def source_crawl_result(
    run_id,
    *,
    scope: str,
    target: str | None,
    pending_routes: int = 0,
    states_pending: int = 0,
) -> dict[str, Any]:
    return {
        "run_id": str(run_id),
        "scope": scope,
        "target": target,
        "pending_routes": pending_routes,
        "states_pending": states_pending,
    }


def write_state_exploration_summary(
    structural_dir: Path,
    *,
    states_pending: int = 0,
) -> None:
    (structural_dir / "state_exploration_summary.json").write_text(
        json.dumps(
            {
                "frontier_pending_count": states_pending,
                "frontier_explored_count": 1,
            }
        ),
        encoding="utf-8",
    )


def attach_crawl_quality(
    canonical_dir: Path,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = quality or certified_crawl_quality()
    manifest_path = canonical_dir / "manifest.json"
    report_path = canonical_dir / "build_report.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest["crawl_execution_quality"] = payload
    report["crawl_execution_quality"] = payload
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return payload
