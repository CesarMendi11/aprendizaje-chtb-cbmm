from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    PipelineJobStatus,
)
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
)
from erp_assistant.persistence.postgres.repositories import PipelineJobRepository
from erp_assistant.orchestration.pipeline.job_service import PipelineJobService
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.orchestration.pipeline.runner import PipelineJobRunner


class FakeCrawlExecutor:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "screen_captured",
            {
                "work_units": 3,
                "routes_visited": 1,
                "states_explored": 2,
                "current_route": target,
            },
        )
        if self.fail:
            raise RuntimeError("fallo controlado del crawler")
        return {
            "run_id": str(job_id),
            "scope": str(getattr(scope, "value", scope)),
            "target": target,
            "functional_screens": 1,
        }


class FakeCanonicalBuildExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "exporting_canonical",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": "canonical-test",
            },
        )
        return {
            "knowledge_version": "canonical-test",
            "source_crawl_job_id": parameters["source_crawl_job_id"],
        }





class FakeCanonicalMergeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "full_candidate_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": "merged-test",
            },
        )
        return {
            "knowledge_version": "merged-test",
            "source_canonical_job_id": parameters["source_canonical_job_id"],
            "snapshot_mode": "full",
        }


class FakeCanonicalImportExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "staging_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": "staging-test",
            },
        )
        return {
            "knowledge_version": "staging-test",
            "source_canonical_job_id": parameters["source_canonical_job_id"],
            "staging_ready": True,
        }


class FakeCanonicalReconciliationExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "reconciled_canonical_ready",
            {"work_units": 4, "progress_total": 4, "knowledge_version": "reconciled-test"},
        )
        return {"knowledge_version": "reconciled-test", "snapshot_mode": "full"}


class FakeProjectionSyncExecutor:
    def __init__(self, target):
        self.target = target
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            f"{self.target}_synced",
            {
                "work_units": 4,
                "progress_total": 4,
                "eligible_items": 21,
            },
        )
        return {
            "target": self.target,
            "active_only": True,
            "knowledge_version": parameters["knowledge_version"],
            "knowledge_version_id": parameters["knowledge_version_id"],
            "eligible_items": 21,
        }



class FakeSemanticInferenceExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, *, job_id, scope, target, parameters, progress):
        self.calls.append((job_id, scope, target, parameters))
        progress(
            "proposal_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "semantic_id": "semantic:test",
                "proposal_status": "pending_review",
            },
        )
        return {
            "target": "semantic_proposal",
            "active_only": True,
            "knowledge_version": parameters["knowledge_version"],
            "knowledge_version_id": parameters["knowledge_version_id"],
            "semantic_id": "semantic:test",
            "proposal_status": "pending_review",
        }


def build_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def seed_active_module_tree(factory):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:test",
            slug="erp-test",
            name="ERP Test",
            profile_name="test",
            base_url="http://erp.test",
            safe_metadata={},
        )
        run = ImportRun(
            erp_id=erp.id,
            source_knowledge_path="knowledge.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="v1",
            status=ImportStatus.SUCCEEDED,
        )
        session.add_all([erp, run])
        session.flush()
        version = KnowledgeVersionRecord(
            erp_id=erp.id,
            import_run_id=run.id,
            schema_version="1.1.0",
            knowledge_version="v1",
            canonical_hash="canonical-hash",
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        session.add(version)
        session.flush()

        def item(canonical_id, entity_type, parent, *, title, route=None, payload=None):
            source_payload = dict(payload or {})
            source_payload.setdefault("id", canonical_id)
            return KnowledgeItem(
                knowledge_version_id=version.id,
                canonical_id=canonical_id,
                entity_type=entity_type,
                parent_canonical_id=parent,
                title=title,
                normalized_title=title.casefold(),
                route=route,
                content_hash=f"hash:{canonical_id}",
                source_payload=source_payload,
                generated_review_status=ReviewStatus.APPROVED,
                current_review_status=ReviewStatus.APPROVED,
            )

        sales = item(
            "module:sales",
            "module",
            erp.id,
            title="Sales",
            payload={
                "name": "Sales",
                "depth": 0,
                "navigation_path": ["Sales"],
                "metadata": {"navigation_origin_path": "#sales"},
            },
        )
        tracking = item(
            "module:tracking",
            "module",
            sales.canonical_id,
            title="Tracking",
            payload={
                "name": "Tracking",
                "depth": 1,
                "navigation_path": ["Sales", "Tracking"],
                "metadata": {
                    "navigation_origin_path": "#sales || #tracking"
                },
            },
        )
        integrations = item(
            "module:integrations",
            "module",
            tracking.canonical_id,
            title="Integrations",
            payload={
                "name": "Integrations",
                "depth": 2,
                "navigation_path": ["Sales", "Tracking", "Integrations"],
                "metadata": {
                    "navigation_origin_path": (
                        "#sales || #tracking || #integrations"
                    )
                },
            },
        )
        orders = item(
            "module:orders",
            "module",
            sales.canonical_id,
            title="Orders",
            payload={
                "name": "Orders",
                "depth": 1,
                "navigation_path": ["Sales", "Orders"],
                "metadata": {
                    "navigation_origin_path": "#sales || #orders"
                },
            },
        )
        screens = [
            item(
                "screen:tracking",
                "screen",
                tracking.canonical_id,
                title="Tracking list",
                route="/sales/tracking",
            ),
            item(
                "screen:external",
                "screen",
                integrations.canonical_id,
                title="External systems",
                route="/sales/tracking/integrations/external",
            ),
            item(
                "screen:orders",
                "screen",
                orders.canonical_id,
                title="Orders",
                route="/sales/orders",
            ),
        ]
        session.add_all([sales, tracking, integrations, orders, *screens])
        return version.id


def test_runner_executes_queued_crawl_and_persists_progress_and_result():
    engine, factory = build_factory()
    version_id = seed_active_module_tree(factory)
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="crawl",
            scope="screen",
            target="/sales/tracking",
            erp_id="erp:test",
            knowledge_version_id=version_id,
            parameters={
                "headless": True,
                "slow_mo": 0,
                "target_screen_id": "screen:tracking",
                "knowledge_version_id": str(version_id),
                "knowledge_version": "v1",
                "erp_id": "erp:test",
            },
        )
        job_id = job.id

    executor = FakeCrawlExecutor()
    PipelineJobRunner(factory, crawl_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.stage == "completed"
        assert stored.progress_current == 3
        assert stored.checkpoint["routes_visited"] == 1
        assert stored.checkpoint["states_explored"] == 2
        assert stored.result_payload["functional_screens"] == 1
        assert stored.finished_at is not None
    assert len(executor.calls) == 1
    assert executor.calls[0][3]["target_screen_title"] == "Tracking list"
    engine.dispose()


def test_runner_marks_failed_crawl_without_exposing_worker_exception_to_api_thread():
    engine, factory = build_factory()
    with factory.begin() as session:
        job = PipelineJobService(session).create(kind="crawl", scope="full")
        job_id = job.id

    PipelineJobRunner(factory, crawl_executor=FakeCrawlExecutor(fail=True)).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.FAILED
        assert stored.stage == "failed"
        assert "fallo controlado" in (stored.error_summary or "")
    engine.dispose()


def test_runner_dispatches_canonical_build_and_persists_total_progress():
    engine, factory = build_factory()
    source_id = "00000000-0000-0000-0000-000000000123"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="canonical_build",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={"source_crawl_job_id": source_id},
        )
        job_id = job.id

    executor = FakeCanonicalBuildExecutor()
    PipelineJobRunner(
        factory, canonical_build_executor=executor
    ).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == 4
        assert stored.progress_total == 4
        assert stored.result_payload["knowledge_version"] == "canonical-test"
        assert stored.checkpoint["knowledge_version"] == "canonical-test"
    assert len(executor.calls) == 1
    engine.dispose()




def test_runner_dispatches_canonical_merge_executor():
    engine, factory = build_factory()
    source_id = "00000000-0000-0000-0000-000000000790"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="canonical_merge",
            scope="module",
            target="module:tracking",
            parameters={"source_canonical_job_id": source_id},
        )
        job_id = job.id

    executor = FakeCanonicalMergeExecutor()
    PipelineJobRunner(factory, canonical_merge_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == 4
        assert stored.progress_total == 4
        assert stored.result_payload["knowledge_version"] == "merged-test"
    assert len(executor.calls) == 1
    engine.dispose()


def test_runner_dispatches_canonical_reconciliation_executor():
    engine, factory = build_factory()
    candidate_id = "00000000-0000-0000-0000-000000000791"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="canonical_reconciliation",
            scope="version",
            erp_id="erp:test",
            knowledge_version_id=uuid.UUID(candidate_id),
            parameters={"candidate_version_id": candidate_id, "erp_id": "erp:test"},
        )
        job_id = job.id

    executor = FakeCanonicalReconciliationExecutor()
    PipelineJobRunner(factory, canonical_reconciliation_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == stored.progress_total == 4
        assert stored.result_payload["knowledge_version"] == "reconciled-test"
    assert len(executor.calls) == 1
    engine.dispose()


def test_runner_dispatches_canonical_import_executor():
    engine, factory = build_factory()
    source_id = "00000000-0000-0000-0000-000000000789"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="canonical_import",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={"source_canonical_job_id": source_id},
        )
        job_id = job.id

    executor = FakeCanonicalImportExecutor()
    PipelineJobRunner(factory, canonical_import_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == 4
        assert stored.progress_total == 4
        assert stored.result_payload["staging_ready"] is True
    assert len(executor.calls) == 1
    engine.dispose()



def test_runner_dispatches_projection_sync_executors():
    engine, factory = build_factory()
    version_id = "00000000-0000-0000-0000-000000000555"
    jobs = []
    with factory.begin() as session:
        for kind in ("neo4j_sync", "chroma_sync", "semantic_sync"):
            job = PipelineJobService(session).create(
                kind=kind,
                scope="version",
                target="active-v1",
                parameters={
                    "active_only": True,
                    "knowledge_version": "active-v1",
                    "knowledge_version_id": version_id,
                },
            )
            jobs.append((kind, job.id))

    neo = FakeProjectionSyncExecutor("neo4j")
    chroma = FakeProjectionSyncExecutor("chromadb")
    semantic = FakeProjectionSyncExecutor("semantic_chromadb")
    runner = PipelineJobRunner(
        factory,
        neo4j_sync_executor=neo,
        chroma_sync_executor=chroma,
        semantic_sync_executor=semantic,
    )
    for _kind, job_id in jobs:
        runner.run(job_id)

    with factory() as session:
        for _kind, job_id in jobs:
            stored = PipelineJobRepository(session).get(job_id)
            assert stored is not None
            assert stored.status == PipelineJobStatus.SUCCEEDED
            assert stored.progress_current == 4
            assert stored.progress_total == 4
            assert stored.result_payload["active_only"] is True
    assert len(neo.calls) == 1
    assert len(chroma.calls) == 1
    assert len(semantic.calls) == 1
    engine.dispose()



def test_runner_dispatches_semantic_inference_executor():
    engine, factory = build_factory()
    version_id = "00000000-0000-0000-0000-000000000777"
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="semantic_inference",
            scope="screen",
            target="/admin/cuentasxcobrar/retenciones",
            parameters={
                "active_only": True,
                "knowledge_version": "active-v1",
                "knowledge_version_id": version_id,
                "screen_knowledge_item_id": "00000000-0000-0000-0000-000000000778",
                "screen_id": "screen:retenciones",
            },
        )
        job_id = job.id

    executor = FakeSemanticInferenceExecutor()
    PipelineJobRunner(factory, semantic_inference_executor=executor).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.progress_current == 4
        assert stored.progress_total == 4
        assert stored.result_payload["semantic_id"] == "semantic:test"
        assert stored.result_payload["proposal_status"] == "pending_review"
    assert len(executor.calls) == 1
    engine.dispose()


def test_runner_revalidates_pinned_module_scope_before_dispatch():
    engine, factory = build_factory()
    version_id = seed_active_module_tree(factory)
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="crawl",
            scope="module",
            target="module:tracking",
            erp_id="erp:test",
            knowledge_version_id=version_id,
            parameters={
                "target_module_id": "module:tracking",
                "knowledge_version_id": str(version_id),
                "knowledge_version": "v1",
                "erp_id": "erp:test",
            },
        )
        job_id = job.id

    executor = FakeCrawlExecutor()
    PipelineJobRunner(factory, crawl_executor=executor).run(job_id)

    assert len(executor.calls) == 1
    _, scope, target, parameters = executor.calls[0]
    assert scope.value == "module"
    assert target == "module:tracking"
    assert parameters["module_scope"]["module_ids"] == [
        "module:tracking",
        "module:integrations",
    ]
    assert parameters["module_scope"]["known_screen_routes"] == [
        "/sales/tracking",
        "/sales/tracking/integrations/external",
    ]
    assert "/sales/orders" not in parameters["module_scope"]["known_screen_routes"]
    assert parameters["module_scope"]["navigation_origin_path"] == [
        "#sales",
        "#tracking",
    ]

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.parameters["module_scope"] == parameters["module_scope"]
        assert stored.parameters["module_scope"]["root_module_id"] == "module:tracking"
        assert stored.parameters["module_scope"]["navigation_origin_path"] == [
            "#sales",
            "#tracking",
        ]
    engine.dispose()


def test_runner_fails_module_job_when_pinned_version_is_no_longer_active():
    engine, factory = build_factory()
    version_id = seed_active_module_tree(factory)
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind="crawl",
            scope="module",
            target="module:tracking",
            erp_id="erp:test",
            knowledge_version_id=version_id,
            parameters={
                "target_module_id": "module:tracking",
                "knowledge_version_id": str(version_id),
                "knowledge_version": "v1",
                "erp_id": "erp:test",
            },
        )
        job_id = job.id
        version = session.get(KnowledgeVersionRecord, version_id)
        version.status = KnowledgeVersionStatus.ARCHIVED

    executor = FakeCrawlExecutor()
    PipelineJobRunner(factory, crawl_executor=executor).run(job_id)

    assert executor.calls == []
    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.FAILED
        assert "ACTIVE" in (stored.error_summary or "")
    engine.dispose()
