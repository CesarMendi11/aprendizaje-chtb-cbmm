from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import src.database.models  # noqa: F401
from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)
from src.database.models import KnowledgeItem, KnowledgeVersionRecord, PipelineJob
from src.database.services import CanonicalImportService, VersionDiffError, VersionDiffService
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.ids import content_hash
from tests.canonical_fixtures import exported_fictional_canonical


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def _candidate_paths(tmp_path, canonical_dir):
    knowledge = json.loads((canonical_dir / "knowledge.json").read_text())
    manifest = json.loads((canonical_dir / "manifest.json").read_text())
    knowledge["knowledge_version"] = "diff-candidate"
    manifest["knowledge_version"] = "diff-candidate"
    manifest["canonical_document_hash"] = content_hash(knowledge)
    knowledge_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "candidate-manifest.json"
    knowledge_path.write_text(json.dumps(knowledge), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return knowledge_path, manifest_path


def _versioned_paths(tmp_path, canonical_dir, knowledge_version):
    knowledge = json.loads((canonical_dir / "knowledge.json").read_text())
    manifest = json.loads((canonical_dir / "manifest.json").read_text())
    knowledge["knowledge_version"] = knowledge_version
    manifest["knowledge_version"] = knowledge_version
    manifest["canonical_document_hash"] = content_hash(knowledge)
    knowledge_path = tmp_path / f"{knowledge_version}.json"
    manifest_path = tmp_path / f"{knowledge_version}-manifest.json"
    knowledge_path.write_text(json.dumps(knowledge), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return knowledge_path, manifest_path


def seed_reconciled(session, tmp_path):
    canonical_dir = exported_fictional_canonical(tmp_path / "reconciled-canonical")
    with session.begin():
        active_result = CanonicalImportService(session).import_canonical(
            canonical_dir / "knowledge.json",
            canonical_dir / "manifest.json",
            canonical_dir / "build_report.json",
        )
    with session.begin():
        raw_result = CanonicalImportService(session).import_canonical(
            *_versioned_paths(tmp_path, canonical_dir, "diff-raw"),
            activate=False,
            create_sync_jobs=False,
        )
        candidate_result = CanonicalImportService(session).import_canonical(
            *_versioned_paths(tmp_path, canonical_dir, "diff-reconciled"),
            activate=False,
            create_sync_jobs=False,
        )
        active = session.get(KnowledgeVersionRecord, uuid.UUID(active_result.version_id))
        raw = session.get(KnowledgeVersionRecord, uuid.UUID(raw_result.version_id))
        candidate = session.get(KnowledgeVersionRecord, uuid.UUID(candidate_result.version_id))
        decision_set_hash = content_hash([])
        source = PipelineJob(
            kind=PipelineJobKind.CANONICAL_RECONCILIATION,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.VERSION,
            target=None,
            profile_name="test",
            erp_id=candidate.erp_id,
            knowledge_version_id=raw.id,
            request_source="test",
            parameters={
                "candidate_version_id": str(raw.id),
                "candidate_knowledge_version": raw.knowledge_version,
                "active_version_id": str(active.id),
                "active_knowledge_version": active.knowledge_version,
                "erp_id": candidate.erp_id,
            },
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={},
            result_payload={
                "erp_id": candidate.erp_id,
                "candidate_origin": "partial_module_merge",
                "raw_candidate_version_id": str(raw.id),
                "raw_candidate_knowledge_version": raw.knowledge_version,
                "base_active_version_id": str(active.id),
                "base_active_knowledge_version": active.knowledge_version,
                "knowledge_version": candidate.knowledge_version,
                "reconciled_knowledge_version": candidate.knowledge_version,
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "retain_from_active_total": 0,
                "confirmed_removed_total": 0,
                "unresolved_total": 0,
                "decision_set_hash": decision_set_hash,
                "decisions": [],
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
        origin = PipelineJob(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.VERSION,
            target=None,
            profile_name="test",
            erp_id=candidate.erp_id,
            knowledge_version_id=candidate.id,
            request_source="test",
            parameters={
                "source_reconciliation_job_id": str(source.id),
                "erp_id": candidate.erp_id,
                "expected_knowledge_version": candidate.knowledge_version,
                "expected_decision_set_hash": decision_set_hash,
                "raw_candidate_version_id": str(raw.id),
                "base_active_version_id": str(active.id),
                "activation_mode": "staging_only",
            },
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={},
            result_payload={
                "source_reconciliation_job_id": str(source.id),
                "scope": "version",
                "target": None,
                "erp_id": candidate.erp_id,
                "knowledge_version_id": str(candidate.id),
                "knowledge_version": candidate.knowledge_version,
                "version_status": "imported",
                "import_result": "imported",
                "staging_ready": True,
                "activation_performed": False,
                "raw_candidate_version_id": str(raw.id),
                "raw_candidate_knowledge_version": raw.knowledge_version,
                "base_active_version_id": str(active.id),
                "base_active_knowledge_version": active.knowledge_version,
                "decision_set_hash": decision_set_hash,
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(origin)
    return active.id, raw.id, candidate.id, source.id, origin.id


class Client:
    def __init__(self, app):
        self.app = app

    def get(self, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 50000)),
                base_url="http://test",
            ) as client:
                return await client.get(path, **kwargs)

        return asyncio.run(send())


def _provenance(session, version, *, full=True, import_result="imported"):
    source = PipelineJob(
        kind=PipelineJobKind.CANONICAL_BUILD,
        status=PipelineJobStatus.SUCCEEDED,
        scope=PipelineJobScope.FULL,
        target=None,
        profile_name="test",
        request_source="test",
        parameters={},
        stage="done",
        progress_current=1,
        progress_total=1,
        checkpoint={},
        result_payload={
            "snapshot_mode": "full" if full else "partial",
            "snapshot_scope": "full",
            "knowledge_version": version.knowledge_version,
        },
        requested_at=datetime.now(timezone.utc),
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    session.add(source)
    session.flush()
    session.add(
        PipelineJob(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.FULL,
            target=None,
            profile_name="test",
            erp_id=version.erp_id,
            knowledge_version_id=version.id,
            request_source="test",
            parameters={
                "source_canonical_job_id": str(source.id),
                "activation_mode": "staging_only",
            },
            stage="done",
            progress_current=1,
            progress_total=1,
            checkpoint={},
            result_payload={
                "import_result": import_result,
                "staging_ready": True,
                "activation_performed": False,
                "knowledge_version": version.knowledge_version,
                "knowledge_version_id": str(version.id),
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
    )


def _skipped_retry(session, version):
    _provenance(session, version, import_result="skipped")


def seed(session, tmp_path):
    canonical_dir = exported_fictional_canonical(tmp_path / "canonical")
    with session.begin():
        active_result = CanonicalImportService(session).import_canonical(
            canonical_dir / "knowledge.json",
            canonical_dir / "manifest.json",
            canonical_dir / "build_report.json",
        )
    with session.begin():
        candidate_result = CanonicalImportService(session).import_canonical(
            *_candidate_paths(tmp_path, canonical_dir), activate=False, create_sync_jobs=False
        )
        active = session.get(KnowledgeVersionRecord, uuid.UUID(active_result.version_id))
        candidate = session.get(KnowledgeVersionRecord, uuid.UUID(candidate_result.version_id))
        _provenance(session, candidate)
        active_screen = session.scalar(
            select(KnowledgeItem)
            .where(
                KnowledgeItem.knowledge_version_id == active.id,
                KnowledgeItem.entity_type == "screen",
            )
            .order_by(KnowledgeItem.canonical_id)
        )
        candidate_screens = list(
            session.scalars(
                select(KnowledgeItem)
                .where(
                    KnowledgeItem.knowledge_version_id == candidate.id,
                    KnowledgeItem.entity_type == "screen",
                )
                .order_by(KnowledgeItem.canonical_id)
            )
        )
        candidate_screens[0].title = "Título distinto sin cambio de hash"
        candidate_screens[1].content_hash = "f" * 64
        removed_id = candidate_screens[2].canonical_id
        session.delete(candidate_screens[2])
        for entity_type, canonical_id in (
            ("screen", "screen:zz-new"),
            ("alias", active_screen.canonical_id),
        ):
            session.add(
                KnowledgeItem(
                    knowledge_version_id=candidate.id,
                    entity_type=entity_type,
                    canonical_id=canonical_id,
                    parent_canonical_id=None,
                    title=f"{entity_type} title",
                    normalized_title=None,
                    route=f"/{entity_type}",
                    content_hash="a" * 64,
                    source_payload={},
                    generated_review_status=ReviewStatus.PENDING_REVIEW,
                    current_review_status=ReviewStatus.PENDING_REVIEW,
                )
            )
    return active.id, candidate.id, removed_id


def test_structural_diff_is_hash_based_ordered_and_read_only(session, tmp_path):
    active_id, candidate_id, removed_id = seed(session, tmp_path)
    before = [
        (item.id, item.current_review_status, item.content_hash, item.title)
        for item in session.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))
    ]
    result = VersionDiffService(session).compare(candidate_id)
    after = [
        (item.id, item.current_review_status, item.content_hash, item.title)
        for item in session.scalars(select(KnowledgeItem).order_by(KnowledgeItem.id))
    ]

    assert before == after
    assert result.active_version_id == str(active_id)
    assert result.totals == {
        "unchanged": result.totals["unchanged"],
        "modified": 1,
        "new": 2,
        "removed": 1,
    }
    assert result.totals["unchanged"] > 0
    assert result.counts_by_entity_type["screen"]["modified"] == 1
    assert result.counts_by_entity_type["screen"]["new"] == 1
    assert result.counts_by_entity_type["screen"]["removed"] == 1
    assert result.counts_by_entity_type["alias"]["new"] == 1
    assert [(item.entity_type, item.canonical_id) for item in result.items] == sorted(
        (item.entity_type, item.canonical_id) for item in result.items
    )
    unchanged = next(
        item
        for item in result.items
        if item.entity_type == "screen"
        and item.candidate_title == "Título distinto sin cambio de hash"
    )
    assert unchanged.change_type == "unchanged"
    assert (
        next(
            item
            for item in result.items
            if item.canonical_id == removed_id and item.entity_type == "screen"
        ).change_type
        == "removed"
    )


def test_diff_uses_originating_import_when_succeeded_skipped_retry_exists(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    with session.begin():
        candidate = session.get(KnowledgeVersionRecord, candidate_id)
        _skipped_retry(session, candidate)

    result = VersionDiffService(session).compare(candidate_id)

    assert result.candidate_version_id == str(candidate_id)


def test_diff_fails_closed_for_candidate_state_active_and_provenance(session, tmp_path):
    active_id, candidate_id, _ = seed(session, tmp_path)
    with pytest.raises(VersionDiffError, match="snapshot_mode"):
        job = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        job.result_payload = {**job.result_payload, "snapshot_mode": "partial"}
        VersionDiffService(session).compare(candidate_id)
    session.rollback()
    with session.begin():
        session.get(KnowledgeVersionRecord, active_id).status = KnowledgeVersionStatus.ARCHIVED
    with pytest.raises(VersionDiffError, match="exactamente una"):
        VersionDiffService(session).compare(candidate_id)
    session.rollback()
    with session.begin():
        candidate = session.get(KnowledgeVersionRecord, candidate_id)
        candidate.status = KnowledgeVersionStatus.ACTIVE
    with pytest.raises(VersionDiffError, match="IMPORTED"):
        VersionDiffService(session).compare(candidate_id)
    with pytest.raises(LookupError):
        VersionDiffService(session).compare(uuid.uuid4())


def test_diff_fails_closed_without_originating_import(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    job = session.scalar(
        select(PipelineJob).where(
            PipelineJob.kind == PipelineJobKind.CANONICAL_IMPORT,
            PipelineJob.knowledge_version_id == candidate_id,
        )
    )
    job.result_payload = {**job.result_payload, "import_result": "skipped"}

    with pytest.raises(VersionDiffError, match="ausente o ambigua"):
        VersionDiffService(session).compare(candidate_id)


def test_diff_api_filters_paginates_and_uses_admin_errors(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'diff.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, candidate_id, _ = seed(session, tmp_path)
    app = create_app(
        replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True),
        semantic_review_session_factory=factory,
    )
    client = Client(app)
    response = client.get(
        f"/api/admin/knowledge-versions/{candidate_id}/diff?change_type=new&limit=1&offset=0"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["totals"]["removed"] == 1
    assert body["total"] == 2 and len(body["items"]) == 1 and body["next_offset"] == 1
    assert body["items"][0]["change_type"] == "new"
    not_found = client.get(f"/api/admin/knowledge-versions/{uuid.uuid4()}/diff")
    assert not_found.status_code == 404
    assert not_found.json()["category"] == "not_found"
    with factory.begin() as session:
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        source.result_payload = {**source.result_payload, "snapshot_mode": "partial"}
    invalid = client.get(f"/api/admin/knowledge-versions/{candidate_id}/diff")
    assert invalid.status_code == 422
    assert invalid.json()["category"] == "invalid_version_diff"
    engine.dispose()


def test_diff_accepts_governed_reconciled_full_candidate(session, tmp_path):
    active_id, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)

    result = VersionDiffService(session).compare(candidate_id)

    assert result.active_version_id == str(active_id)
    assert result.candidate_version_id == str(candidate_id)
    assert result.candidate_origin == "reconciled_full"
    assert result.totals["removed"] == 0
    assert sum(result.totals.values()) > 0


@pytest.mark.parametrize(
    "tamper",
    (
        "decision_hash",
        "base_active",
        "unresolved",
        "source_parameters",
        "unresolved_review",
    ),
)
def test_diff_reconciled_provenance_fails_closed(session, tmp_path, tamper):
    _, _, candidate_id, source_id, origin_id = seed_reconciled(session, tmp_path)
    with session.begin():
        source = session.get(PipelineJob, source_id)
        origin = session.get(PipelineJob, origin_id)
        if tamper == "decision_hash":
            origin.result_payload = {**origin.result_payload, "decision_set_hash": "f" * 64}
        elif tamper == "base_active":
            source.result_payload = {
                **source.result_payload,
                "base_active_version_id": str(uuid.uuid4()),
            }
        elif tamper == "unresolved":
            source.result_payload = {**source.result_payload, "unresolved_total": 1}
        elif tamper == "source_parameters":
            source.parameters = {
                **source.parameters,
                "candidate_knowledge_version": "tampered",
            }
        else:
            decisions = [
                {
                    "entity_type": "screen",
                    "canonical_id": "screen:fake",
                    "active_item_id": str(uuid.uuid4()),
                    "candidate_item_id": None,
                    "screen_id": "screen:fake",
                    "decision": "retain_from_active",
                    "reason": "test",
                    "removal_confirmation": "unconfirmed",
                    "requires_human_review": True,
                    "review_set_id": None,
                    "review_decision_id": None,
                    "review_action_id": None,
                    "review_revision": None,
                }
            ]
            decision_hash = content_hash(decisions)
            source.result_payload = {
                **source.result_payload,
                "retain_from_active_total": 1,
                "decisions": decisions,
                "decision_set_hash": decision_hash,
            }
            origin.parameters = {
                **origin.parameters,
                "expected_decision_set_hash": decision_hash,
            }
            origin.result_payload = {
                **origin.result_payload,
                "decision_set_hash": decision_hash,
            }

    with pytest.raises(VersionDiffError):
        VersionDiffService(session).compare(candidate_id)
