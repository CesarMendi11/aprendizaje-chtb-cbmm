from __future__ import annotations

import argparse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from erp_assistant.config.paths import PROJECT_ROOT
from erp_assistant.persistence.postgres.repositories import PipelineJobRepository
from erp_assistant.persistence.postgres.session import session_scope
from scripts.common.database import database_engine
from scripts.experiments.common import (
    project_relative,
    redact_sensitive,
    utc_now_iso,
    write_json_atomic,
)


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def _artifact_inventory(result_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result_payload:
        return None
    raw_root = result_payload.get("artifact_root")
    if not raw_root:
        return None
    root = Path(str(raw_root))
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    if not root.is_dir():
        return {
            "root": project_relative(root),
            "exists": False,
            "files": 0,
            "bytes": 0,
            "by_suffix": {},
        }
    files = [item for item in root.rglob("*") if item.is_file()]
    by_suffix: dict[str, int] = {}
    for item in files:
        suffix = item.suffix.casefold() or "<no_suffix>"
        by_suffix[suffix] = by_suffix.get(suffix, 0) + 1
    return {
        "root": project_relative(root),
        "exists": True,
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
        "by_suffix": dict(sorted(by_suffix.items())),
    }


def build_job_snapshot(job) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "snapshot_type": "pipeline_job",
        "captured_at": utc_now_iso(),
        "job": {
            "id": str(job.id),
            "kind": job.kind.value,
            "status": job.status.value,
            "scope": job.scope.value,
            "target": job.target,
            "profile_name": job.profile_name,
            "erp_id": job.erp_id,
            "knowledge_version_id": (
                str(job.knowledge_version_id) if job.knowledge_version_id else None
            ),
            "request_source": job.request_source,
            "stage": job.stage,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "parameters": redact_sensitive(job.parameters or {}),
            "checkpoint": redact_sensitive(job.checkpoint or {}),
            "result_payload": redact_sensitive(job.result_payload or {}),
            "error_summary": job.error_summary,
            "requested_at": job.requested_at.isoformat() if job.requested_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "queue_wait_ms": _duration_ms(job.requested_at, job.started_at),
            "execution_ms": _duration_ms(job.started_at, job.finished_at),
            "wall_clock_ms": _duration_ms(job.requested_at, job.finished_at),
        },
        "artifacts": _artifact_inventory(job.result_payload),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exporta un PipelineJob y sus métricas temporales para evaluación."
    )
    parser.add_argument("job_id", help="UUID del PipelineJob a capturar.")
    parser.add_argument(
        "--output",
        help=("Ruta JSON de salida. Por defecto: experiments/results/jobs/<job-id>.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        job_id = uuid.UUID(args.job_id)
    except ValueError:
        raise SystemExit("job_id no es un UUID válido") from None

    with session_scope(database_engine()) as session:
        job = PipelineJobRepository(session).get(job_id)
        if job is None:
            raise SystemExit(f"PipelineJob no encontrado: {job_id}")
        snapshot = build_job_snapshot(job)

    output = args.output or f"experiments/results/jobs/{job_id}.json"
    destination = write_json_atomic(output, snapshot)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
