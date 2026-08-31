from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import erp_assistant.persistence.postgres.models  # noqa: F401
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)
from erp_assistant.persistence.postgres.models import KnowledgeItem, KnowledgeVersionRecord
from erp_assistant.persistence.postgres.repositories import PipelineJobRepository
from erp_assistant.orchestration.pipeline.job_service import PipelineJobService
from erp_assistant.structural.services.removal_reconciliation_review_service import (
    RemovalReconciliationReviewService,
)
from erp_assistant.structural.canonical import CanonicalKnowledgeExporter, CanonicalKnowledgeRepository
from erp_assistant.structural.canonical.ids import content_hash
from erp_assistant.orchestration.pipeline.executors.canonical_reconciliation import (
    CanonicalReconciliationJobExecutionError,
    CanonicalReconciliationJobExecutor,
)
from erp_assistant.orchestration.pipeline.runner import PipelineJobRunner
from tests.fixtures.removal_review import resolve_all_removals
from tests.structural.canonical.test_canonical_reconciliation_service import (
    _materializable_full_candidate,
    _materializable_partial_candidate,
)
from tests.structural.governance.test_version_diff_service import seed


def _factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'reconciliation.sqlite3'}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _pins(factory, active_id, candidate_id):
    with factory() as session:
        active = session.get(KnowledgeVersionRecord, active_id)
        candidate = session.get(KnowledgeVersionRecord, candidate_id)
        return {
            "candidate_version_id": str(candidate.id),
            "candidate_knowledge_version": candidate.knowledge_version,
            "active_version_id": str(active.id),
            "active_knowledge_version": active.knowledge_version,
            "erp_id": active.erp_id,
        }


def _resolve_removals(factory, candidate_id):
    with factory.begin() as session:
        resolve_all_removals(session, candidate_id)


def test_reconciliation_executor_exports_isolated_full_artifact_with_provenance(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, removed = _materializable_partial_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    _resolve_removals(factory, candidate_id)
    raw_dir = tmp_path / "data" / "runs" / "pipeline" / "raw-crawl"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "knowledge.json"
    raw_path.write_text('{"raw": true}\n', encoding="utf-8")
    with factory() as session:
        raw_before = {
            (item.entity_type, item.canonical_id, item.content_hash)
            for item in session.scalars(
                select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == candidate_id)
            )
        }

    result = CanonicalReconciliationJobExecutor(
        factory, project_root=tmp_path, runs_root="data/runs/pipeline"
    ).execute(
        job_id=uuid.uuid4(),
        scope=PipelineJobScope.VERSION,
        target=None,
        parameters=pins,
    )

    knowledge_path = tmp_path / result["knowledge_path"]
    repository = CanonicalKnowledgeRepository(knowledge_path)
    assert repository.knowledge.knowledge_version == result["reconciled_knowledge_version"]
    assert result["snapshot_mode"] == result["snapshot_scope"] == "full"
    assert result["raw_candidate_version_id"] == str(candidate_id)
    assert result["base_active_version_id"] == str(active_id)
    assert result["raw_candidate_knowledge_version"] == pins["candidate_knowledge_version"]
    assert result["base_active_knowledge_version"] == pins["active_knowledge_version"]
    assert result["candidate_origin"] == "partial_module_merge"
    assert result["decision_set_hash"] == content_hash(result["decisions"])
    assert result["decisions"] == sorted(
        result["decisions"], key=lambda value: (value["entity_type"], value["canonical_id"])
    )
    assert all(not value["requires_human_review"] for value in result["decisions"])
    assert all(value["review_set_id"] for value in result["decisions"])
    assert all(value["review_decision_id"] for value in result["decisions"])
    assert all(value["review_action_id"] for value in result["decisions"])
    assert all(value["review_revision"] == 1 for value in result["decisions"])
    assert raw_path.read_text(encoding="utf-8") == '{"raw": true}\n'

    manifest = json.loads((tmp_path / result["manifest_path"]).read_text(encoding="utf-8"))
    report = json.loads((tmp_path / result["build_report_path"]).read_text(encoding="utf-8"))
    for payload in (manifest["reconciliation"], report["reconciliation"]):
        assert payload["raw_candidate_version_id"] == str(candidate_id)
        assert payload["base_active_version_id"] == str(active_id)
        assert payload["erp_id"] == pins["erp_id"]
        assert payload["decision_set_hash"] == result["decision_set_hash"]
    assert manifest["snapshot"] == {"mode": "full", "scope": "full", "target": None,
                                    "target_module_id": None, "target_screen_id": None,
                                    "base_knowledge_version_id": None,
                                    "base_knowledge_version": None, "erp_id": None}
    assert report["decision_set_hash"] == result["decision_set_hash"]
    for collection, item in (
        ("controls", removed["control"]),
        ("ui_states", removed["ui_state"]),
        ("transitions", removed["transition"]),
        ("table_columns", removed["table_column"]),
    ):
        assert item.canonical_id in {
            value.id for value in getattr(repository.knowledge, collection)
        }
    with factory() as session:
        raw_after = {
            (item.entity_type, item.canonical_id, item.content_hash)
            for item in session.scalars(
                select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == candidate_id)
            )
        }
    assert raw_after == raw_before
    engine.dispose()



def test_reconciliation_executor_supports_governed_full_candidate(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, removed = _materializable_full_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    with factory.begin() as session:
        resolve_all_removals(
            session,
            candidate_id,
            confirmed_remove={("control", removed["control"].canonical_id)},
        )

    result = CanonicalReconciliationJobExecutor(
        factory, project_root=tmp_path, runs_root="data/runs/pipeline"
    ).execute(
        job_id=uuid.uuid4(),
        scope=PipelineJobScope.VERSION,
        target=None,
        parameters=pins,
    )

    assert result["candidate_origin"] == "full_canonical"
    assert result["unresolved_total"] == 0
    assert result["confirmed_removed_total"] == 1
    assert result["retain_from_active_total"] == len(removed) - 1
    assert result["decision_set_hash"] == content_hash(result["decisions"])
    assert all(value["review_action_id"] for value in result["decisions"])
    engine.dispose()

def test_reconciliation_executor_fails_closed_for_pinned_context_and_unresolved(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    executor = CanonicalReconciliationJobExecutor(
        factory, project_root=tmp_path, runs_root="data/runs/pipeline"
    )
    with pytest.raises(CanonicalReconciliationJobExecutionError, match="RAW"):
        executor.execute(
            job_id=uuid.uuid4(),
            scope="version",
            target=None,
            parameters={**pins, "candidate_knowledge_version": "wrong"},
        )
    with pytest.raises(CanonicalReconciliationJobExecutionError, match="ERP"):
        executor.execute(
            job_id=uuid.uuid4(),
            scope="version",
            target=None,
            parameters={**pins, "erp_id": "erp:wrong"},
        )
    with factory.begin() as session:
        session.get(KnowledgeVersionRecord, active_id).status = KnowledgeVersionStatus.ARCHIVED
    with pytest.raises(CanonicalReconciliationJobExecutionError, match="ACTIVE"):
        executor.execute(
            job_id=uuid.uuid4(), scope="version", target=None, parameters=pins
        )
    engine.dispose()

    unresolved_root = tmp_path / "unresolved"
    unresolved_root.mkdir()
    engine, factory = _factory(unresolved_root)
    with factory() as session:
        active_id, candidate_id, _ = seed(session, unresolved_root)
    pins = _pins(factory, active_id, candidate_id)
    with factory.begin() as session:
        RemovalReconciliationReviewService(session).prepare(candidate_id)
    job_id = uuid.uuid4()
    with pytest.raises(CanonicalReconciliationJobExecutionError, match="resolver todas"):
        CanonicalReconciliationJobExecutor(
            factory, project_root=unresolved_root, runs_root="data/runs/pipeline"
        ).execute(job_id=job_id, scope="version", target=None, parameters=pins)
    output_dir = (
        unresolved_root
        / "data"
        / "runs"
        / "pipeline"
        / "reconciliation"
        / str(job_id)
    )
    assert not output_dir.exists()
    engine.dispose()


def test_reconciliation_executor_rejects_tampered_exported_manifest(tmp_path, monkeypatch):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    _resolve_removals(factory, candidate_id)
    original_export = CanonicalKnowledgeExporter.export

    def tampered_export(self, *args, **kwargs):
        output = original_export(self, *args, **kwargs)
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["reconciliation"]["decision_set_hash"] = "tampered"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return output

    monkeypatch.setattr(CanonicalKnowledgeExporter, "export", tampered_export)
    with pytest.raises(CanonicalReconciliationJobExecutionError, match="inconsistentes"):
        CanonicalReconciliationJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ).execute(job_id=uuid.uuid4(), scope="version", target=None, parameters=pins)
    engine.dispose()


def test_reconciliation_executor_hashes_the_single_resolved_review_plan(tmp_path, monkeypatch):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    _resolve_removals(factory, candidate_id)
    original_resolved = RemovalReconciliationReviewService.resolved_plan
    plans = []

    def tracked_resolved(service, *args, **kwargs):
        plan = original_resolved(service, *args, **kwargs)
        plans.append(plan)
        return plan

    monkeypatch.setattr(
        RemovalReconciliationReviewService,
        "resolved_plan",
        tracked_resolved,
    )
    executor = CanonicalReconciliationJobExecutor(
        factory, project_root=tmp_path, runs_root="data/runs/pipeline"
    )
    result = executor.execute(
        job_id=uuid.uuid4(), scope="version", target=None, parameters=pins
    )

    assert len(plans) == 1
    expected_decisions = executor._normalized_decisions(plans[0])
    assert result["decisions"] == expected_decisions
    assert result["decision_set_hash"] == content_hash(expected_decisions)
    assert all(not value["requires_human_review"] for value in expected_decisions)
    manifest = json.loads((tmp_path / result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["reconciliation"]["decision_set_hash"] == result["decision_set_hash"]
    engine.dispose()


def test_runner_executes_real_canonical_reconciliation_job_end_to_end(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    _resolve_removals(factory, candidate_id)
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind=PipelineJobKind.CANONICAL_RECONCILIATION,
            scope=PipelineJobScope.VERSION,
            erp_id=pins["erp_id"],
            knowledge_version_id=candidate_id,
            parameters=pins,
        )
        job_id = job.id

    PipelineJobRunner(
        factory,
        canonical_reconciliation_executor=CanonicalReconciliationJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ),
    ).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.SUCCEEDED
        assert stored.knowledge_version_id == candidate_id
        assert stored.checkpoint["candidate_version_id"] == str(candidate_id)
        assert stored.checkpoint["active_version_id"] == str(active_id)
        assert stored.result_payload["raw_candidate_version_id"] == str(candidate_id)
        assert "knowledge_version_id" not in stored.result_payload
        assert stored.result_payload["raw_candidate_knowledge_version"] == pins[
            "candidate_knowledge_version"
        ]
        assert stored.result_payload["base_active_version_id"] == str(active_id)
        assert (
            stored.result_payload["knowledge_version"]
            == stored.result_payload["reconciled_knowledge_version"]
        )
        assert (tmp_path / stored.result_payload["knowledge_path"]).is_file()
        exported = CanonicalKnowledgeRepository(
            tmp_path / stored.result_payload["knowledge_path"]
        ).knowledge
        assert exported.knowledge_version == stored.result_payload["knowledge_version"]
        json.dumps(stored.checkpoint)
        json.dumps(stored.result_payload)
    engine.dispose()


def test_runner_fails_closed_when_raw_candidate_job_pin_is_inconsistent(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as session:
        active_id, candidate_id, _ = _materializable_partial_candidate(session, tmp_path)
    pins = _pins(factory, active_id, candidate_id)
    with factory.begin() as session:
        job = PipelineJobService(session).create(
            kind=PipelineJobKind.CANONICAL_RECONCILIATION,
            scope=PipelineJobScope.VERSION,
            erp_id=pins["erp_id"],
            knowledge_version_id=active_id,
            parameters=pins,
        )
        job_id = job.id

    PipelineJobRunner(
        factory,
        canonical_reconciliation_executor=CanonicalReconciliationJobExecutor(
            factory, project_root=tmp_path, runs_root="data/runs/pipeline"
        ),
    ).run(job_id)

    with factory() as session:
        stored = PipelineJobRepository(session).get(job_id)
        assert stored is not None
        assert stored.status == PipelineJobStatus.FAILED
        assert "candidate_version_id inconsistente" in stored.error_summary
    output_dir = tmp_path / "data" / "runs" / "pipeline" / "reconciliation" / str(job_id)
    assert not output_dir.exists()
    engine.dispose()
