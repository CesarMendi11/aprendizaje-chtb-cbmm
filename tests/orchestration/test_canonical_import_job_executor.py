from __future__ import annotations

import json
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from erp_assistant.orchestration.pipeline.executors.canonical_import import (
    CanonicalImportJobExecutionError,
    CanonicalImportJobExecutor,
)
from erp_assistant.orchestration.pipeline.executors.canonical_reconciliation import (
    CanonicalReconciliationJobExecutor,
)
from erp_assistant.orchestration.pipeline.job_service import PipelineJobService
from erp_assistant.orchestration.pipeline.runner import PipelineJobRunner
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)
from erp_assistant.persistence.postgres.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    PipelineJob,
    SyncJob,
)
from erp_assistant.persistence.postgres.repositories import PipelineJobRepository
from erp_assistant.structural.canonical.ids import content_hash
from erp_assistant.structural.services.canonical_import_service import CanonicalImportService
from tests.fixtures.canonical import exported_fictional_canonical
from tests.fixtures.crawl_quality import attach_crawl_quality, certified_crawl_quality
from tests.fixtures.removal_review import resolve_all_removals
from tests.structural.canonical.test_canonical_reconciliation_service import (
    _materializable_partial_candidate,
)


def _factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'staging.sqlite3'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _copy_canonical(tmp_path, crawl_id: uuid.UUID):
    target = tmp_path / "data" / "runs" / "pipeline" / str(crawl_id) / "processed" / "canonical"
    exported_fictional_canonical(target)
    attach_crawl_quality(
        target,
        certified_crawl_quality(run_id=crawl_id, scope="full", target=None),
    )
    return target


def _reconciliation_source(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, raw_id, _ = _materializable_partial_candidate(session, tmp_path)
    with factory.begin() as session:
        resolve_all_removals(session, raw_id)
    with factory() as session:
        active = session.get(KnowledgeVersionRecord, active_id)
        raw = session.get(KnowledgeVersionRecord, raw_id)
        pins = {
            "candidate_version_id": str(raw.id),
            "candidate_knowledge_version": raw.knowledge_version,
            "active_version_id": str(active.id),
            "active_knowledge_version": active.knowledge_version,
            "erp_id": active.erp_id,
        }
    with factory.begin() as session:
        source = PipelineJobService(session).create(
            kind=PipelineJobKind.CANONICAL_RECONCILIATION,
            scope=PipelineJobScope.VERSION,
            erp_id=pins["erp_id"],
            knowledge_version_id=raw_id,
            parameters=pins,
        )
        source_id = source.id
    PipelineJobRunner(
        factory,
        canonical_reconciliation_executor=CanonicalReconciliationJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ),
    ).run(source_id)
    with factory() as session:
        source = PipelineJobRepository(session).get(source_id)
        assert source is not None and source.status == PipelineJobStatus.SUCCEEDED
        source_result = dict(source.result_payload)
    import_params = {
        "source_reconciliation_job_id": str(source_id),
        "erp_id": pins["erp_id"],
        "expected_knowledge_version": source_result["reconciled_knowledge_version"],
        "expected_decision_set_hash": source_result["decision_set_hash"],
        "raw_candidate_version_id": str(raw_id),
        "base_active_version_id": str(active_id),
        "activation_mode": "staging_only",
    }
    return engine, factory, active_id, raw_id, source_id, source_result, import_params


def _run_reconciliation_import(factory, tmp_path, raw_id, params):
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            scope=PipelineJobScope.VERSION,
            erp_id=params["erp_id"],
            knowledge_version_id=raw_id,
            parameters=params,
        )
        job_id = job.id
    PipelineJobRunner(
        factory,
        canonical_import_executor=CanonicalImportJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ),
    ).run(job_id)
    with factory() as session:
        job = PipelineJobRepository(session).get(job_id)
        assert job is not None
        return job


def test_reconciliation_source_imports_staging_and_preserves_governed_lineage(tmp_path):
    engine, factory, active_id, raw_id, source_id, source_result, params = _reconciliation_source(
        tmp_path
    )
    stored = _run_reconciliation_import(factory, tmp_path, raw_id, params)

    assert stored.status == PipelineJobStatus.SUCCEEDED
    assert stored.stage == "completed"
    assert stored.progress_current == stored.progress_total == 4
    assert stored.parameters["source_reconciliation_job_id"] == str(source_id)
    assert stored.result_payload["source_reconciliation_job_id"] == str(source_id)
    assert stored.knowledge_version_id != raw_id
    assert stored.result_payload["knowledge_version_id"] == str(stored.knowledge_version_id)
    assert (
        stored.result_payload["knowledge_version"] == source_result["reconciled_knowledge_version"]
    )
    assert stored.result_payload["raw_candidate_version_id"] == str(raw_id)
    assert stored.result_payload["base_active_version_id"] == str(active_id)
    assert stored.result_payload["decision_set_hash"] == source_result["decision_set_hash"]
    with factory() as session:
        reconciled = session.get(KnowledgeVersionRecord, stored.knowledge_version_id)
        active = session.get(KnowledgeVersionRecord, active_id)
        raw = session.get(KnowledgeVersionRecord, raw_id)
        assert reconciled.status == KnowledgeVersionStatus.IMPORTED
        assert reconciled.knowledge_version == source_result["reconciled_knowledge_version"]
        assert active.status == KnowledgeVersionStatus.ACTIVE
        assert raw.status == KnowledgeVersionStatus.IMPORTED
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(KnowledgeItem.knowledge_version_id == reconciled.id)
            )
            == source_result["reconciled_item_total"]
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(SyncJob)
                .where(SyncJob.knowledge_version_id == reconciled.id)
            )
            == 0
        )
        source = session.get(PipelineJob, source_id)
        assert source.knowledge_version_id == raw_id
        assert source.result_payload["base_active_version_id"] == str(active_id)
    engine.dispose()


def test_reconciliation_import_has_no_fallible_progress_after_commit(tmp_path):
    engine, factory, active_id, raw_id, source_id, source_result, params = _reconciliation_source(
        tmp_path
    )
    stages = []

    def progress(stage, payload):
        if stage == "reconciled_staging_ready":
            raise RuntimeError("simulated final checkpoint failure")
        stages.append((stage, payload))

    result = CanonicalImportJobExecutor(
        factory, project_root=tmp_path, runs_root="data/runs/pipeline"
    ).execute(
        job_id=uuid.uuid4(),
        scope="version",
        target=None,
        parameters=params,
        progress=progress,
    )

    assert [stage for stage, _ in stages] == [
        "loading_reconciliation_source",
        "importing_reconciled_staging",
    ]
    assert result["knowledge_version_id"]
    assert result["knowledge_version"] == source_result["reconciled_knowledge_version"]
    assert result["source_reconciliation_job_id"] == str(source_id)
    assert result["decision_set_hash"] == source_result["decision_set_hash"]
    assert result["raw_candidate_version_id"] == str(raw_id)
    assert result["base_active_version_id"] == str(active_id)
    with factory() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(result["knowledge_version_id"]))
        assert version is not None and version.status == KnowledgeVersionStatus.IMPORTED
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(KnowledgeItem.knowledge_version_id == version.id)
            )
            == source_result["reconciled_item_total"]
        )
        assert (
            session.get(KnowledgeVersionRecord, active_id).status == KnowledgeVersionStatus.ACTIVE
        )
        assert session.get(KnowledgeVersionRecord, raw_id).status == KnowledgeVersionStatus.IMPORTED
        assert (
            session.scalar(
                select(func.count())
                .select_from(SyncJob)
                .where(SyncJob.knowledge_version_id == version.id)
            )
            == 0
        )
    engine.dispose()


def test_reconciliation_source_import_is_idempotent_and_rejects_invalid_provenance(tmp_path):
    engine, factory, _active_id, raw_id, source_id, source_result, params = _reconciliation_source(
        tmp_path
    )
    first = _run_reconciliation_import(factory, tmp_path, raw_id, params)
    second = _run_reconciliation_import(factory, tmp_path, raw_id, params)
    assert first.status == second.status == PipelineJobStatus.SUCCEEDED
    assert first.knowledge_version_id == second.knowledge_version_id
    with factory() as session:
        versions = list(
            session.scalars(
                select(KnowledgeVersionRecord).where(
                    KnowledgeVersionRecord.knowledge_version
                    == source_result["reconciled_knowledge_version"]
                )
            )
        )
        assert len(versions) == 1

    executor = CanonicalImportJobExecutor(
        factory, project_root=tmp_path, runs_root="data/runs/pipeline"
    )
    with pytest.raises(CanonicalImportJobExecutionError, match="pins"):
        executor.execute(
            job_id=uuid.uuid4(),
            scope="version",
            target=None,
            parameters={**params, "expected_decision_set_hash": "wrong"},
        )
    with factory.begin() as session:
        source = session.get(PipelineJob, source_id)
        source.kind = PipelineJobKind.CANONICAL_BUILD
    with pytest.raises(CanonicalImportJobExecutionError, match="source reconciliation"):
        executor.execute(job_id=uuid.uuid4(), scope="version", target=None, parameters=params)
    engine.dispose()


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("source_not_succeeded", "source reconciliation"),
        ("active_changed", "RAW candidate/ACTIVE"),
        ("raw_changed", "RAW candidate/ACTIVE"),
        ("path_escape", "canonical_dir"),
        ("tampered_manifest", "artifacts reconciliation"),
        ("tampered_counts", "artifacts reconciliation"),
        ("source_parameters", "parameters del source"),
        ("unresolved_review_decision", "Removal HITL resuelto"),
    ],
)
def test_reconciliation_source_import_fails_closed_for_runtime_provenance(
    tmp_path, mutation, match
):
    case_root = tmp_path / mutation
    case_root.mkdir()
    engine, factory, active_id, raw_id, source_id, source_result, params = _reconciliation_source(
        case_root
    )
    if mutation == "source_not_succeeded":
        with factory.begin() as session:
            session.get(PipelineJob, source_id).status = PipelineJobStatus.FAILED
    elif mutation == "active_changed":
        with factory.begin() as session:
            session.get(KnowledgeVersionRecord, active_id).status = KnowledgeVersionStatus.ARCHIVED
    elif mutation == "raw_changed":
        with factory.begin() as session:
            session.get(KnowledgeVersionRecord, raw_id).status = KnowledgeVersionStatus.ARCHIVED
    elif mutation == "path_escape":
        with factory.begin() as session:
            source = session.get(PipelineJob, source_id)
            source.result_payload = {**source.result_payload, "canonical_dir": "outside"}
    elif mutation == "source_parameters":
        with factory.begin() as session:
            source = session.get(PipelineJob, source_id)
            source.parameters = {**source.parameters, "candidate_knowledge_version": "wrong"}
    elif mutation == "unresolved_review_decision":
        with factory.begin() as session:
            source = session.get(PipelineJob, source_id)
            decisions = [dict(value) for value in source.result_payload["decisions"]]
            decisions[0]["requires_human_review"] = True
            decision_hash = content_hash(decisions)
            source.result_payload = {
                **source.result_payload,
                "decisions": decisions,
                "decision_set_hash": decision_hash,
            }
            params["expected_decision_set_hash"] = decision_hash
    elif mutation == "tampered_counts":
        report_path = case_root / source_result["build_report_path"]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["counts"]["reconciled_item_total"] += 1
        report_path.write_text(json.dumps(report), encoding="utf-8")
    else:
        manifest_path = case_root / source_result["manifest_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["reconciliation"]["decision_set_hash"] = "tampered"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CanonicalImportJobExecutionError, match=match):
        CanonicalImportJobExecutor(
            factory, project_root=case_root, runs_root="data/runs/pipeline"
        ).execute(job_id=uuid.uuid4(), scope="version", target=None, parameters=params)
    engine.dispose()


@pytest.mark.parametrize(
    "job_version,job_erp,match",
    [
        ("active", "source", "raw_candidate_version_id inconsistente"),
        ("raw", "wrong", "erp_id inconsistente"),
    ],
)
def test_runner_rejects_inconsistent_reconciliation_import_job_metadata(
    tmp_path, job_version, job_erp, match
):
    engine, factory, active_id, raw_id, _source_id, source_result, params = _reconciliation_source(
        tmp_path
    )
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            scope=PipelineJobScope.VERSION,
            erp_id=params["erp_id"] if job_erp == "source" else "erp:wrong",
            knowledge_version_id=active_id if job_version == "active" else raw_id,
            parameters=params,
        )
        job_id = job.id
    PipelineJobRunner(
        factory,
        canonical_import_executor=CanonicalImportJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ),
    ).run(job_id)
    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None and stored.status == PipelineJobStatus.FAILED
        assert match in stored.error_summary
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeVersionRecord)
                .where(
                    KnowledgeVersionRecord.knowledge_version
                    == source_result["reconciled_knowledge_version"]
                )
            )
            == 0
        )
    engine.dispose()


def test_reconciliation_import_rolls_back_writes_after_post_import_failure(tmp_path, monkeypatch):
    engine, factory, active_id, raw_id, _source_id, source_result, params = _reconciliation_source(
        tmp_path
    )
    with factory() as session:
        sync_jobs_before = session.scalar(select(func.count()).select_from(SyncJob))
    original_import = CanonicalImportService.import_canonical

    def inconsistent_import(service, *args, **kwargs):
        imported = original_import(service, *args, **kwargs)
        return replace(imported, version_id=str(uuid.uuid4()))

    monkeypatch.setattr(CanonicalImportService, "import_canonical", inconsistent_import)
    with pytest.raises(CanonicalImportJobExecutionError, match="importada es inconsistente"):
        CanonicalImportJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ).execute(job_id=uuid.uuid4(), scope="version", target=None, parameters=params)
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeVersionRecord)
                .where(
                    KnowledgeVersionRecord.knowledge_version
                    == source_result["reconciled_knowledge_version"]
                )
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(KnowledgeItem.knowledge_version_id.not_in([active_id, raw_id]))
            )
            == 0
        )
        assert (
            session.get(KnowledgeVersionRecord, active_id).status == KnowledgeVersionStatus.ACTIVE
        )
        assert session.get(KnowledgeVersionRecord, raw_id).status == KnowledgeVersionStatus.IMPORTED
        assert session.scalar(select(func.count()).select_from(SyncJob)) == sync_jobs_before
    engine.dispose()


def test_reconciliation_source_import_rejects_expected_knowledge_version_mismatch(tmp_path):
    engine, factory, _active_id, _raw_id, _source_id, _source_result, params = (
        _reconciliation_source(tmp_path)
    )
    with pytest.raises(CanonicalImportJobExecutionError, match="pins"):
        CanonicalImportJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ).execute(
            job_id=uuid.uuid4(),
            scope="version",
            target=None,
            parameters={**params, "expected_knowledge_version": "wrong"},
        )
    engine.dispose()


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
            "expected_crawl_execution_quality": certified_crawl_quality(
                run_id=crawl_id, scope="full", target=None
            ),
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
    exported_fictional_canonical(tmp_path / "outside")

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
                "expected_crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="full", target=None
                ),
                "knowledge_path": str(canonical_dir.relative_to(tmp_path) / "knowledge.json"),
                "manifest_path": str(canonical_dir.relative_to(tmp_path) / "manifest.json"),
                "build_report_path": str(canonical_dir.relative_to(tmp_path) / "build_report.json"),
            },
        )
    except CanonicalImportJobExecutionError as exc:
        assert "debe fusionarse" in str(exc)
    else:
        raise AssertionError("Se esperaba rechazo de canonical parcial")
    engine.dispose()


def test_canonical_import_executor_rejects_legacy_artifacts_without_crawl_quality(tmp_path):
    engine, factory = _factory(tmp_path)
    crawl_id = uuid.uuid4()
    canonical_dir = _copy_canonical(tmp_path, crawl_id)
    for name in ("manifest.json", "build_report.json"):
        path = canonical_dir / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("crawl_execution_quality", None)
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CanonicalImportJobExecutionError, match="calidad de crawl certificada"):
        CanonicalImportJobExecutor(
            factory,
            project_root=tmp_path,
            runs_root="data/runs/pipeline",
        ).execute(
            job_id=uuid.uuid4(),
            scope=PipelineJobScope.FULL,
            target=None,
            parameters={
                "source_canonical_job_id": str(uuid.uuid4()),
                "source_crawl_job_id": str(crawl_id),
                "expected_crawl_execution_quality": certified_crawl_quality(
                    run_id=crawl_id, scope="full", target=None
                ),
                "knowledge_path": str(canonical_dir.relative_to(tmp_path) / "knowledge.json"),
                "manifest_path": str(canonical_dir.relative_to(tmp_path) / "manifest.json"),
                "build_report_path": str(canonical_dir.relative_to(tmp_path) / "build_report.json"),
            },
        )
    engine.dispose()
