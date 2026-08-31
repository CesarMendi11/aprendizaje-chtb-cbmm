from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus, PipelineJobScope
from erp_assistant.persistence.postgres.models import KnowledgeVersionRecord
from erp_assistant.structural.services.canonical_import_service import CanonicalImportService
from erp_assistant.structural.canonical import (
    CanonicalKnowledgeExporter,
    CanonicalKnowledgeRepository,
    CanonicalSnapshotContext,
)
from erp_assistant.structural.canonical.models import CanonicalKnowledgeBase
from erp_assistant.orchestration.pipeline.executors.canonical_import import (
    CanonicalImportJobExecutionError,
    CanonicalImportJobExecutor,
)
from erp_assistant.orchestration.pipeline.executors.canonical_merge import (
    CanonicalMergeJobExecutionError,
    CanonicalMergeJobExecutor,
)
from tests.fixtures.crawl_quality import attach_crawl_quality, certified_crawl_quality


def _module(module_id, name, *, parent=None, depth=0, path=None):
    return {
        "id": module_id,
        "erp_id": "erp:test",
        "parent_module_id": parent,
        "depth": depth,
        "navigation_path": path or [name],
        "name": name,
        "normalized_name": name.casefold(),
    }


def _screen(screen_id, title, route, module_id=None):
    return {
        "id": screen_id,
        "erp_id": "erp:test",
        "module_id": module_id,
        "title": title,
        "normalized_title": title.casefold(),
        "route": route,
    }


def _knowledge(*, version: str, partial: bool = False) -> CanonicalKnowledgeBase:
    sales = _module("module:sales", "Sales")
    tracking = _module(
        "module:tracking",
        "Tracking",
        parent="module:sales",
        depth=1,
        path=["Sales", "Tracking"],
    )
    if partial:
        modules = [sales, tracking]
        screens = [
            _screen("screen:tracking", "Tracking refreshed", "/tracking", "module:tracking"),
            _screen("screen:provider", "Provider", "/tracking/provider", "module:tracking"),
        ]
    else:
        orders = _module(
            "module:orders",
            "Orders",
            parent="module:sales",
            depth=1,
            path=["Sales", "Orders"],
        )
        modules = [sales, orders, tracking]
        screens = [
            _screen("screen:orders", "Orders", "/orders", "module:orders"),
            _screen("screen:tracking", "Tracking", "/tracking", "module:tracking"),
        ]
    return CanonicalKnowledgeBase.model_validate(
        {
            "schema_version": "1.1.0",
            "knowledge_version": version,
            "generated_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
            "generator_version": "test",
            "source_profile": "fictional",
            "source_artifacts": ["screen_index.json"],
            "source_artifact_hashes": {"screen_index.json": f"hash-{version}"},
            "erp_system": {
                "id": "erp:test",
                "slug": "test",
                "name": "Test ERP",
                "profile_name": "fictional",
            },
            "modules": modules,
            "screens": screens,
            "statistics": {
                "modules": len(modules),
                "screens": len(screens),
                "ui_states": 0,
                "fields": 0,
                "controls": 0,
                "tables": 0,
                "table_columns": 0,
                "links": 0,
                "events": 0,
                "transitions": 0,
                "evidence": 0,
            },
        }
    )


def _setup(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'merge.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    base = _knowledge(version="base-v1")
    base_dir = tmp_path / "base"
    CanonicalKnowledgeExporter().export(
        base,
        base_dir,
        snapshot_context=CanonicalSnapshotContext.full(),
    )
    with factory.begin() as session:
        imported = CanonicalImportService(session).import_canonical(
            base_dir / "knowledge.json",
            base_dir / "manifest.json",
            base_dir / "build_report.json",
            activate=True,
            create_sync_jobs=False,
        )
        base_id = uuid.UUID(imported.version_id)

    crawl_id = uuid.uuid4()
    partial = _knowledge(version="partial-v1", partial=True)
    snapshot = CanonicalSnapshotContext(
        mode="partial",
        scope="module",
        target="module:tracking",
        target_module_id="module:tracking",
        base_knowledge_version_id=str(base_id),
        base_knowledge_version="base-v1",
        erp_id="erp:test",
    )
    partial_dir = (
        tmp_path
        / "data"
        / "runs"
        / "pipeline"
        / str(crawl_id)
        / "processed"
        / "canonical"
    )
    CanonicalKnowledgeExporter().export(
        partial,
        partial_dir,
        snapshot_context=snapshot,
    )
    quality = attach_crawl_quality(
        partial_dir,
        certified_crawl_quality(
            run_id=crawl_id, scope="module", target="module:tracking"
        ),
    )
    params = {
        "source_canonical_job_id": str(uuid.uuid4()),
        "source_crawl_job_id": str(crawl_id),
        "knowledge_path": str(partial_dir.relative_to(tmp_path) / "knowledge.json"),
        "manifest_path": str(partial_dir.relative_to(tmp_path) / "manifest.json"),
        "build_report_path": str(partial_dir.relative_to(tmp_path) / "build_report.json"),
        "expected_partial_knowledge_version": "partial-v1",
        "target_module_id": "module:tracking",
        "base_knowledge_version_id": str(base_id),
        "base_knowledge_version": "base-v1",
        "erp_id": "erp:test",
        "expected_crawl_execution_quality": quality,
    }
    return engine, factory, base_id, crawl_id, params


def test_merge_executor_materializes_exact_active_base_and_exports_full_candidate(tmp_path):
    engine, factory, base_id, _crawl_id, params = _setup(tmp_path)
    events = []
    result = CanonicalMergeJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope=PipelineJobScope.MODULE,
        target="module:tracking",
        parameters=params,
        progress=lambda stage, payload: events.append((stage, payload)),
    )

    merged = CanonicalKnowledgeRepository(tmp_path / result["knowledge_path"]).knowledge
    assert {item.id for item in merged.modules} == {
        "module:sales",
        "module:orders",
        "module:tracking",
    }
    screens = {item.id: item for item in merged.screens}
    assert set(screens) == {"screen:orders", "screen:tracking", "screen:provider"}
    assert screens["screen:orders"].title == "Orders"
    assert screens["screen:tracking"].title == "Tracking refreshed"
    assert result["snapshot_mode"] == "full"
    assert result["base_knowledge_version_id"] == str(base_id)
    assert [stage for stage, _ in events] == [
        "loading_partial_canonical",
        "materializing_active_base",
        "exporting_full_candidate",
        "full_candidate_ready",
    ]
    manifest = json.loads((tmp_path / result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["snapshot"]["mode"] == "full"
    assert manifest["merge"]["base_knowledge_version_id"] == str(base_id)
    assert manifest["merge"]["target_module_id"] == "module:tracking"
    assert manifest["crawl_execution_quality"] == result["crawl_execution_quality"]
    report = json.loads((tmp_path / result["build_report_path"]).read_text(encoding="utf-8"))
    assert report["crawl_execution_quality"] == result["crawl_execution_quality"]

    with factory() as session:
        base = session.get(KnowledgeVersionRecord, base_id)
        assert base.status == KnowledgeVersionStatus.ACTIVE
    engine.dispose()


def test_merge_executor_fails_when_pinned_base_is_archived(tmp_path):
    engine, factory, base_id, _crawl_id, params = _setup(tmp_path)
    with factory.begin() as session:
        session.get(KnowledgeVersionRecord, base_id).status = KnowledgeVersionStatus.ARCHIVED

    with pytest.raises(CanonicalMergeJobExecutionError, match="ACTIVE"):
        CanonicalMergeJobExecutor(
            factory,
            project_root=tmp_path,
            runs_root="data/runs/pipeline",
        ).execute(
            job_id=uuid.uuid4(),
            scope="module",
            target="module:tracking",
            parameters=params,
        )
    engine.dispose()


def test_import_of_merged_candidate_rechecks_pinned_base_active(tmp_path):
    engine, factory, base_id, crawl_id, params = _setup(tmp_path)
    merge_result = CanonicalMergeJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope="module",
        target="module:tracking",
        parameters=params,
    )
    with factory.begin() as session:
        session.get(KnowledgeVersionRecord, base_id).status = KnowledgeVersionStatus.ARCHIVED

    with pytest.raises(CanonicalImportJobExecutionError, match="ya no está ACTIVE"):
        CanonicalImportJobExecutor(
            factory,
            project_root=tmp_path,
            runs_root="data/runs/pipeline",
        ).execute(
            job_id=uuid.uuid4(),
            scope="full",
            target=None,
            parameters={
                "source_canonical_job_id": str(uuid.uuid4()),
                "source_crawl_job_id": str(crawl_id),
                "knowledge_path": merge_result["knowledge_path"],
                "manifest_path": merge_result["manifest_path"],
                "build_report_path": merge_result["build_report_path"],
                "expected_knowledge_version": merge_result["knowledge_version"],
                "requires_active_base": True,
                "base_knowledge_version_id": str(base_id),
                "base_knowledge_version": "base-v1",
                "erp_id": "erp:test",
                "merged_from_scope": "module",
                "merged_target_module_id": "module:tracking",
                "expected_crawl_execution_quality": merge_result[
                    "crawl_execution_quality"
                ],
            },
        )
    engine.dispose()


def test_import_of_merged_candidate_creates_staging_while_exact_base_remains_active(tmp_path):
    engine, factory, base_id, crawl_id, params = _setup(tmp_path)
    merge_result = CanonicalMergeJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope="module",
        target="module:tracking",
        parameters=params,
    )

    result = CanonicalImportJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope="full",
        target=None,
        parameters={
            "source_canonical_job_id": str(uuid.uuid4()),
            "source_crawl_job_id": str(crawl_id),
            "knowledge_path": merge_result["knowledge_path"],
            "manifest_path": merge_result["manifest_path"],
            "build_report_path": merge_result["build_report_path"],
            "expected_knowledge_version": merge_result["knowledge_version"],
            "requires_active_base": True,
            "base_knowledge_version_id": str(base_id),
            "base_knowledge_version": "base-v1",
            "erp_id": "erp:test",
            "merged_from_scope": "module",
            "merged_target_module_id": "module:tracking",
            "expected_crawl_execution_quality": merge_result[
                "crawl_execution_quality"
            ],
        },
    )

    assert result["staging_ready"] is True
    assert result["version_status"] == "imported"
    assert result["base_knowledge_version_id"] == str(base_id)
    with factory() as session:
        base = session.get(KnowledgeVersionRecord, base_id)
        staging = session.get(
            KnowledgeVersionRecord, uuid.UUID(result["knowledge_version_id"])
        )
        assert base.status == KnowledgeVersionStatus.ACTIVE
        assert staging.status == KnowledgeVersionStatus.IMPORTED
    engine.dispose()


def _setup_screen(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'screen-merge.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    base = _knowledge(version="base-v1")
    base_dir = tmp_path / "screen-base"
    CanonicalKnowledgeExporter().export(
        base,
        base_dir,
        snapshot_context=CanonicalSnapshotContext.full(),
    )
    with factory.begin() as session:
        imported = CanonicalImportService(session).import_canonical(
            base_dir / "knowledge.json",
            base_dir / "manifest.json",
            base_dir / "build_report.json",
            activate=True,
            create_sync_jobs=False,
        )
        base_id = uuid.UUID(imported.version_id)

    crawl_id = uuid.uuid4()
    partial = _knowledge(version="partial-v1", partial=True)
    snapshot = CanonicalSnapshotContext(
        mode="partial",
        scope="screen",
        target="/tracking",
        target_screen_id="screen:tracking",
        base_knowledge_version_id=str(base_id),
        base_knowledge_version="base-v1",
        erp_id="erp:test",
    )
    partial_dir = (
        tmp_path
        / "data"
        / "runs"
        / "pipeline"
        / str(crawl_id)
        / "processed"
        / "canonical"
    )
    CanonicalKnowledgeExporter().export(
        partial,
        partial_dir,
        snapshot_context=snapshot,
    )
    quality = attach_crawl_quality(
        partial_dir,
        certified_crawl_quality(
            run_id=crawl_id, scope="screen", target="/tracking"
        ),
    )
    params = {
        "source_canonical_job_id": str(uuid.uuid4()),
        "source_crawl_job_id": str(crawl_id),
        "knowledge_path": str(partial_dir.relative_to(tmp_path) / "knowledge.json"),
        "manifest_path": str(partial_dir.relative_to(tmp_path) / "manifest.json"),
        "build_report_path": str(partial_dir.relative_to(tmp_path) / "build_report.json"),
        "expected_partial_knowledge_version": "partial-v1",
        "target_screen_id": "screen:tracking",
        "base_knowledge_version_id": str(base_id),
        "base_knowledge_version": "base-v1",
        "erp_id": "erp:test",
        "expected_crawl_execution_quality": quality,
    }
    return engine, factory, base_id, crawl_id, params


def test_screen_merge_executor_replaces_only_pinned_screen_and_exports_full_candidate(tmp_path):
    engine, factory, base_id, _crawl_id, params = _setup_screen(tmp_path)
    result = CanonicalMergeJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope=PipelineJobScope.SCREEN,
        target="/tracking",
        parameters=params,
    )

    merged = CanonicalKnowledgeRepository(tmp_path / result["knowledge_path"]).knowledge
    screens = {item.id: item for item in merged.screens}
    assert set(screens) == {"screen:orders", "screen:tracking"}
    assert screens["screen:tracking"].title == "Tracking refreshed"
    assert screens["screen:orders"].title == "Orders"
    assert result["merged_from_scope"] == "screen"
    assert result["target_screen_id"] == "screen:tracking"
    assert result["target_module_id"] is None
    assert result["base_knowledge_version_id"] == str(base_id)
    manifest = json.loads((tmp_path / result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["merge"]["scope"] == "screen"
    assert manifest["merge"]["target"] == "/tracking"
    assert manifest["merge"]["target_screen_id"] == "screen:tracking"
    assert manifest["crawl_execution_quality"] == result["crawl_execution_quality"]
    engine.dispose()


def test_screen_merged_candidate_imports_as_staging_with_exact_base_pin(tmp_path):
    engine, factory, base_id, crawl_id, params = _setup_screen(tmp_path)
    merge_result = CanonicalMergeJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope="screen",
        target="/tracking",
        parameters=params,
    )

    result = CanonicalImportJobExecutor(
        factory,
        project_root=tmp_path,
        runs_root="data/runs/pipeline",
    ).execute(
        job_id=uuid.uuid4(),
        scope="full",
        target=None,
        parameters={
            "source_canonical_job_id": str(uuid.uuid4()),
            "source_crawl_job_id": str(crawl_id),
            "knowledge_path": merge_result["knowledge_path"],
            "manifest_path": merge_result["manifest_path"],
            "build_report_path": merge_result["build_report_path"],
            "expected_knowledge_version": merge_result["knowledge_version"],
            "requires_active_base": True,
            "base_knowledge_version_id": str(base_id),
            "base_knowledge_version": "base-v1",
            "erp_id": "erp:test",
            "merged_from_scope": "screen",
            "merged_target_screen_id": "screen:tracking",
            "expected_crawl_execution_quality": merge_result[
                "crawl_execution_quality"
            ],
        },
    )

    assert result["staging_ready"] is True
    assert result["version_status"] == "imported"
    with factory() as session:
        assert session.get(KnowledgeVersionRecord, base_id).status == KnowledgeVersionStatus.ACTIVE
    engine.dispose()


def test_merge_executor_rejects_partial_without_versioned_crawl_quality(tmp_path):
    engine, factory, _base_id, _crawl_id, params = _setup(tmp_path)
    for key in ("manifest_path", "build_report_path"):
        path = tmp_path / params[key]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("crawl_execution_quality", None)
        path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CanonicalMergeJobExecutionError, match="calidad de crawl certificada"):
        CanonicalMergeJobExecutor(
            factory,
            project_root=tmp_path,
            runs_root="data/runs/pipeline",
        ).execute(
            job_id=uuid.uuid4(),
            scope=PipelineJobScope.MODULE,
            target="module:tracking",
            parameters=params,
        )
    engine.dispose()
