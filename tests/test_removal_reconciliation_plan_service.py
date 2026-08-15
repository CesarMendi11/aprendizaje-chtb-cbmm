from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import src.database.models  # noqa: F401
from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import PipelineJobKind
from src.database.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    PipelineJob,
    ReviewAction,
    SyncJob,
)
from src.database.services import (
    RemovalReconciliationPlanError,
    RemovalReconciliationPlanService,
)
from src.database.services.structural_review_package_service import (
    StructuralReviewPackageService,
)
from tests.test_version_diff_service import seed


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def partial_candidate(session, tmp_path):
    active_id, candidate_id, _ = seed(session, tmp_path)
    with session.begin():
        active = session.get(KnowledgeVersionRecord, active_id)
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        source.kind = PipelineJobKind.CANONICAL_MERGE
        source.result_payload = {**source.result_payload, "merged_from_scope": "module"}
        source.result_payload = {
            **source.result_payload,
            "base_knowledge_version_id": str(active.id),
            "base_knowledge_version": active.knowledge_version,
            "erp_id": active.erp_id,
        }
    return active_id, candidate_id


def test_partial_removed_is_retained_and_plan_is_read_only(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    before = (
        [(item.id, item.status) for item in session.scalars(select(KnowledgeVersionRecord))],
        [
            (item.id, item.content_hash, item.current_review_status, item.review_revision)
            for item in session.scalars(select(KnowledgeItem))
        ],
        session.query(ReviewAction).count(),
        session.query(PipelineJob).count(),
        session.query(SyncJob).count(),
    )
    plan = RemovalReconciliationPlanService(session).build(candidate_id)
    after = (
        [(item.id, item.status) for item in session.scalars(select(KnowledgeVersionRecord))],
        [
            (item.id, item.content_hash, item.current_review_status, item.review_revision)
            for item in session.scalars(select(KnowledgeItem))
        ],
        session.query(ReviewAction).count(),
        session.query(PipelineJob).count(),
        session.query(SyncJob).count(),
    )
    assert before == after
    assert plan.candidate_origin == "partial_module_merge"
    assert plan.removal_total == plan.retain_from_active_total
    assert plan.confirmed_removed_total == 0 and plan.unresolved_total == 0
    assert all(item.decision == "retain_from_active" for item in plan.decisions)
    assert all(
        item.reason == "not_observed_in_partial_module_crawl" and item.requires_human_review
        for item in plan.decisions
    )


def test_full_candidate_removals_are_unresolved_and_bad_partial_base_fails(session, tmp_path):
    active_id, candidate_id, _ = seed(session, tmp_path)
    full = RemovalReconciliationPlanService(session).build(candidate_id)
    assert full.unresolved_total == full.removal_total
    assert full.confirmed_removed_total == 0
    session.rollback()
    with session.begin():
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        source.kind = PipelineJobKind.CANONICAL_MERGE
        source.result_payload = {**source.result_payload, "merged_from_scope": "module"}
        source.result_payload = {
            **source.result_payload,
            "base_knowledge_version_id": str(uuid.uuid4()),
            "base_knowledge_version": "wrong",
            "erp_id": "wrong",
        }
    with pytest.raises(RemovalReconciliationPlanError, match="base ACTIVE"):
        RemovalReconciliationPlanService(session).build(candidate_id)
    assert session.get(KnowledgeVersionRecord, active_id) is not None


def test_partial_policy_applies_equally_to_removed_entity_types(session, tmp_path):
    _, candidate_id = partial_candidate(session, tmp_path)
    with session.begin():
        for entity_type in ("control", "ui_state", "transition", "table_column"):
            item = session.scalar(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == candidate_id,
                    KnowledgeItem.entity_type == entity_type,
                )
            )
            assert item is not None, entity_type
            session.delete(item)
    plan = RemovalReconciliationPlanService(session).build(candidate_id)
    selected = [
        item
        for item in plan.decisions
        if item.entity_type in {"control", "ui_state", "transition", "table_column"}
    ]
    assert {item.entity_type for item in selected} == {
        "control",
        "ui_state",
        "transition",
        "table_column",
    }
    assert all(
        item.decision == "retain_from_active"
        and item.reason == "not_observed_in_partial_module_crawl"
        and item.requires_human_review
        for item in selected
    )


def test_package_identity_and_removed_correlation_fail_closed(session, tmp_path, monkeypatch):
    _, candidate_id = partial_candidate(session, tmp_path)
    original = StructuralReviewPackageService.build

    def inconsistent(self, *args, **kwargs):
        return replace(original(self, *args, **kwargs), active_version_id=str(uuid.uuid4()))

    monkeypatch.setattr(StructuralReviewPackageService, "build", inconsistent)
    with pytest.raises(RemovalReconciliationPlanError, match="inconsistentes"):
        RemovalReconciliationPlanService(session).build(candidate_id)
    monkeypatch.setattr(StructuralReviewPackageService, "build", original)

    def missing_removed(self, *args, **kwargs):
        package = original(self, *args, **kwargs)
        package_with_removed = next(
            value
            for value in package.packages
            if any(change.change_type == "removed" for change in value.changes)
        )
        changes = tuple(
            change for change in package_with_removed.changes if change.change_type != "removed"
        )
        return replace(
            package,
            packages=tuple(
                (
                    replace(value, changes=changes)
                    if value.screen_id == package_with_removed.screen_id
                    else value
                )
                for value in package.packages
            ),
        )

    monkeypatch.setattr(StructuralReviewPackageService, "build", missing_removed)
    with pytest.raises(RemovalReconciliationPlanError, match="correlacionarse"):
        RemovalReconciliationPlanService(session).build(candidate_id)


def test_duplicate_removed_unknown_confirmation_and_partial_base_fields_fail_closed(
    session, tmp_path, monkeypatch
):
    _, candidate_id = partial_candidate(session, tmp_path)
    original = StructuralReviewPackageService.build

    def duplicate(self, *args, **kwargs):
        package = original(self, *args, **kwargs)
        target = next(
            value
            for value in package.packages
            if any(change.change_type == "removed" for change in value.changes)
        )
        removed = next(change for change in target.changes if change.change_type == "removed")
        return replace(package, unscoped_changes=(*package.unscoped_changes, removed))

    monkeypatch.setattr(StructuralReviewPackageService, "build", duplicate)
    with pytest.raises(RemovalReconciliationPlanError, match="duplicado"):
        RemovalReconciliationPlanService(session).build(candidate_id)
    monkeypatch.setattr(StructuralReviewPackageService, "build", original)

    def unknown_confirmation(self, *args, **kwargs):
        package = original(self, *args, **kwargs)
        target = next(
            value
            for value in package.packages
            if any(change.change_type == "removed" for change in value.changes)
        )
        changes = tuple(
            (
                replace(change, removal_confirmation="unknown")
                if change.change_type == "removed"
                else change
            )
            for change in target.changes
        )
        return replace(
            package,
            packages=tuple(
                replace(value, changes=changes) if value.screen_id == target.screen_id else value
                for value in package.packages
            ),
        )

    monkeypatch.setattr(StructuralReviewPackageService, "build", unknown_confirmation)
    with pytest.raises(RemovalReconciliationPlanError, match="removal_confirmation"):
        RemovalReconciliationPlanService(session).build(candidate_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("base_knowledge_version_id", str(uuid.uuid4())),
        ("base_knowledge_version", "wrong"),
        ("erp_id", "wrong"),
    ],
)
def test_each_partial_base_provenance_field_fails_closed(session, tmp_path, field, value):
    _, candidate_id = partial_candidate(session, tmp_path)
    with session.begin():
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_MERGE)
        )
        source.result_payload = {**source.result_payload, field: value}
    with pytest.raises(RemovalReconciliationPlanError, match="base ACTIVE"):
        RemovalReconciliationPlanService(session).build(candidate_id)


def test_removal_reconciliation_plan_api(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'plan.sqlite3'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, candidate_id = partial_candidate(session, tmp_path)
    app = create_app(
        replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True),
        semantic_review_session_factory=factory,
    )

    async def get(path):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            base_url="http://test",
        ) as client:
            return await client.get(path)

    ok = asyncio.run(get(f"/api/admin/removal-reconciliation-plans/{candidate_id}"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["retain_from_active_total"] == ok.json()["removal_total"]
    missing = asyncio.run(get(f"/api/admin/removal-reconciliation-plans/{uuid.uuid4()}"))
    assert missing.status_code == 404
    with factory.begin() as session:
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_MERGE)
        )
        source.result_payload = {**source.result_payload, "base_knowledge_version": "wrong"}
    invalid = asyncio.run(get(f"/api/admin/removal-reconciliation-plans/{candidate_id}"))
    assert invalid.status_code == 422
    assert invalid.json()["category"] == "invalid_removal_reconciliation_plan"
    engine.dispose()


def test_screen_partial_removed_is_retained_with_screen_specific_reason(session, tmp_path):
    active_id, candidate_id, _ = seed(session, tmp_path)
    with session.begin():
        active = session.get(KnowledgeVersionRecord, active_id)
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        source.kind = PipelineJobKind.CANONICAL_MERGE
        source.result_payload = {
            **source.result_payload,
            "merged_from_scope": "screen",
            "target_screen_id": "screen:retenciones",
            "base_knowledge_version_id": str(active.id),
            "base_knowledge_version": active.knowledge_version,
            "erp_id": active.erp_id,
        }

    plan = RemovalReconciliationPlanService(session).build(candidate_id)
    assert plan.candidate_origin == "partial_screen_merge"
    assert plan.removal_total == plan.retain_from_active_total
    assert plan.unresolved_total == 0
    assert all(
        item.reason == "not_observed_in_partial_screen_crawl"
        and item.requires_human_review
        for item in plan.decisions
    )
