from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.database.base import Base
from src.database.enums import KnowledgeVersionStatus, PipelineJobScope
from src.database.models import KnowledgeItem, KnowledgeVersionRecord, SyncJob
from src.pipeline.canonical_import_job_executor import (
    CanonicalImportJobExecutionError,
    CanonicalImportJobExecutor,
)

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data" / "processed" / "canonical"


def _factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staging.sqlite3'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _copy_canonical(tmp_path, crawl_id: uuid.UUID):
    target = tmp_path / "data" / "runs" / "pipeline" / str(crawl_id) / "processed" / "canonical"
    target.mkdir(parents=True)
    for name in ("knowledge.json", "manifest.json", "build_report.json"):
        shutil.copy2(CANONICAL / name, target / name)
    return target


def test_canonical_import_executor_creates_non_active_staging_without_sync_jobs(tmp_path):
    engine, factory = _factory(tmp_path)
    crawl_id = uuid.uuid4()
    canonical_job_id = uuid.uuid4()
    canonical_dir = _copy_canonical(tmp_path, crawl_id)
    events = []

    result = CanonicalImportJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope=PipelineJobScope.SCREEN,
        target="/admin/cuentasxcobrar/retenciones",
        parameters={
            "source_canonical_job_id": str(canonical_job_id),
            "source_crawl_job_id": str(crawl_id),
            "knowledge_path": str(canonical_dir.relative_to(tmp_path) / "knowledge.json"),
            "manifest_path": str(canonical_dir.relative_to(tmp_path) / "manifest.json"),
            "build_report_path": str(canonical_dir.relative_to(tmp_path) / "build_report.json"),
        },
        progress=lambda stage, payload: events.append((stage, payload)),
    )

    assert result["import_result"] == "imported"
    assert result["version_status"] == "imported"
    assert result["staging_ready"] is True
    assert result["activation_performed"] is False
    assert result["sync_jobs_created"] is False
    assert result["sync_jobs_present"] == 0
    assert [stage for stage, _ in events] == [
        "loading_canonical",
        "validating_import",
        "importing_staging",
        "staging_ready",
    ]

    with factory() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(result["knowledge_version_id"]))
        assert version is not None
        assert version.status == KnowledgeVersionStatus.IMPORTED
        assert session.scalar(select(func.count()).select_from(SyncJob)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeItem)) == result["items"]
    engine.dispose()


def test_canonical_import_executor_rejects_artifacts_outside_source_run(tmp_path):
    engine, factory = _factory(tmp_path)
    crawl_id = uuid.uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("knowledge.json", "manifest.json", "build_report.json"):
        shutil.copy2(CANONICAL / name, outside / name)

    executor = CanonicalImportJobExecutor(factory, project_root=tmp_path)
    try:
        executor.execute(
            job_id=uuid.uuid4(),
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={
                "source_canonical_job_id": str(uuid.uuid4()),
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": "outside/knowledge.json",
                "manifest_path": "outside/manifest.json",
                "build_report_path": "outside/build_report.json",
            },
        )
    except CanonicalImportJobExecutionError as exc:
        assert "fuera del crawl aislado" in str(exc)
    else:
        raise AssertionError("Se esperaba rechazo de path fuera del run")
    engine.dispose()


def test_canonical_import_executor_rejects_partial_snapshot(tmp_path):
    engine, factory = _factory(tmp_path)
    crawl_id = uuid.uuid4()
    canonical_dir = _copy_canonical(tmp_path, crawl_id)
    manifest_path = canonical_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["snapshot"] = {
        "mode": "partial",
        "scope": "module",
        "target": "module:tracking",
        "target_module_id": "module:tracking",
        "base_knowledge_version_id": str(uuid.uuid4()),
        "base_knowledge_version": "active-v1",
        "erp_id": manifest["erp"]["id"],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    executor = CanonicalImportJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    )
    try:
        executor.execute(
            job_id=uuid.uuid4(),
            scope=PipelineJobScope.MODULE,
            target="module:tracking",
            parameters={
                "source_canonical_job_id": str(uuid.uuid4()),
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": str(
                    canonical_dir.relative_to(tmp_path) / "knowledge.json"
                ),
                "manifest_path": str(
                    canonical_dir.relative_to(tmp_path) / "manifest.json"
                ),
                "build_report_path": str(
                    canonical_dir.relative_to(tmp_path) / "build_report.json"
                ),
            },
        )
    except CanonicalImportJobExecutionError as exc:
        assert "debe fusionarse" in str(exc)
    else:
        raise AssertionError("Se esperaba rechazo de canonical parcial")
    engine.dispose()
