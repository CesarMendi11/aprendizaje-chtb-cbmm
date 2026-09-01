from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

import erp_assistant.persistence.postgres.models  # noqa: F401
from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    SyncStatus,
    SyncTarget,
)
from erp_assistant.persistence.postgres.models import (
    KnowledgeItem,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
    PipelineJob,
    SyncJob,
)
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.services.canonical_import_service import CanonicalImportService
from erp_assistant.structural.services.knowledge_promotion_service import (
    KnowledgePromotionBlockedError,
    KnowledgePromotionService,
)
from erp_assistant.structural.services.knowledge_review_service import KnowledgeReviewService
from tests.fixtures.canonical import exported_fictional_canonical
from tests.fixtures.crawl_quality import certified_crawl_quality


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


@pytest.fixture
def staged(session, tmp_path):
    canonical_dir = exported_fictional_canonical(tmp_path / "canonical")
    with session.begin():
        imported = CanonicalImportService(session).import_canonical(
            canonical_dir / "knowledge.json",
            canonical_dir / "manifest.json",
            canonical_dir / "build_report.json",
            activate=False,
            create_sync_jobs=False,
        )
        version = session.get(KnowledgeVersionRecord, uuid.UUID(imported.version_id))
        crawl_id = uuid.uuid4()
        quality = certified_crawl_quality(run_id=crawl_id, scope="full", target=None)
        source = PipelineJob(
            kind=PipelineJobKind.CANONICAL_BUILD,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.FULL,
            target=None,
            profile_name="synthetic",
            request_source="admin_api",
            parameters={"source_crawl_job_id": str(crawl_id)},
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={"knowledge_version": version.knowledge_version},
            result_payload={
                "source_crawl_job_id": str(crawl_id),
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "knowledge_version": version.knowledge_version,
                "crawl_execution_quality": quality,
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.flush()
        import_job = PipelineJob(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.FULL,
            target=None,
            profile_name="synthetic",
            erp_id=version.erp_id,
            knowledge_version_id=version.id,
            request_source="admin_api",
            parameters={
                "source_canonical_job_id": str(source.id),
                "activation_mode": "staging_only",
                "expected_crawl_execution_quality": quality,
            },
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={"version_status": "imported"},
            result_payload={
                "staging_ready": True,
                "activation_performed": False,
                "knowledge_version": version.knowledge_version,
                "knowledge_version_id": str(version.id),
                "crawl_execution_quality": quality,
            },
            requested_at=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        session.add(import_job)
    return version.id


def _approve_required(session: Session, version_id: uuid.UUID) -> None:
    items = list(
        session.scalars(
            select(KnowledgeItem).where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.entity_type.in_(("erp_system", "module")),
            )
        )
    )
    session.rollback()
    with session.begin():
        service = KnowledgeReviewService(session)
        for item in items:
            service.approve(item.id, reviewer="reviewer:test")


def test_bootstrap_gate_blocks_pending_required_structure(session, staged):
    assessment = KnowledgePromotionService(session).assess(staged)
    assert assessment.promotable is False
    assert assessment.bootstrap_promotion is True
    codes = {item.code for item in assessment.blockers}
    assert "required_pending_review" in codes
    assert assessment.pipeline_import_job_id is not None
    assert assessment.source_canonical_job_id is not None


def test_bootstrap_promotion_activates_and_creates_projection_jobs(session, staged):
    _approve_required(session, staged)
    assessment = KnowledgePromotionService(session).assess(staged)
    assert assessment.promotable is True
    session.rollback()

    with session.begin():
        result = KnowledgePromotionService(session).promote_bootstrap(
            staged,
            reviewer="reviewer:test",
            reason="Primera ACTIVE vNext validada.",
            expected_knowledge_version=assessment.knowledge_version,
        )

    version = session.get(KnowledgeVersionRecord, staged)
    assert version.status == KnowledgeVersionStatus.ACTIVE
    jobs = list(session.scalars(select(SyncJob).where(SyncJob.knowledge_version_id == staged)))
    assert len(jobs) == 2
    assert {job.status for job in jobs} == {SyncStatus.PENDING}
    promotion = session.scalar(select(KnowledgeVersionPromotion))
    assert promotion is not None
    assert promotion.reviewer_subject == "reviewer:test"
    assert promotion.gate_snapshot["promotable"] is True
    assert result.previous_active_version_id is None


def test_promotion_is_fail_closed_after_version_is_already_active(session, staged):
    _approve_required(session, staged)
    assessment = KnowledgePromotionService(session).assess(staged)
    assert assessment.promotable is True
    session.rollback()

    with session.begin():
        KnowledgePromotionService(session).promote_bootstrap(
            staged,
            reviewer="reviewer:test",
            reason="Primera ACTIVE vNext validada.",
            expected_knowledge_version=assessment.knowledge_version,
        )

    assessment = KnowledgePromotionService(session).assess(staged)
    assert assessment.promotable is False
    assert any(blocker.code == "version_not_imported" for blocker in assessment.blockers)
    session.rollback()

    with pytest.raises(KnowledgePromotionBlockedError):
        with session.begin():
            KnowledgePromotionService(session).promote_bootstrap(
                staged,
                reviewer="reviewer:test",
                reason="No debe promoverse dos veces.",
                expected_knowledge_version=assessment.knowledge_version,
            )


def test_bootstrap_gate_blocks_module_without_reproducible_navigation(session, staged):
    module = session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == staged,
            KnowledgeItem.entity_type == "module",
        )
    )
    assert module is not None
    payload = dict(module.source_payload)
    payload["metadata"] = {}
    session.execute(
        update(KnowledgeItem).where(KnowledgeItem.id == module.id).values(source_payload=payload)
    )
    session.commit()

    _approve_required(session, staged)
    assessment = KnowledgePromotionService(session).assess(staged)

    assert assessment.promotable is False
    blocker = next(
        item for item in assessment.blockers if item.code == "module_navigation_unreproducible"
    )
    assert blocker.entity_type == "module"
    assert blocker.count == 1


def test_replacement_gate_uses_reconciled_diff_and_archives_previous_active(tmp_path):
    from tests.structural.governance.test_version_diff_service import seed_reconciled

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        active_id, _, candidate_id, source_id, _ = seed_reconciled(session, tmp_path)

        assessment = KnowledgePromotionService(session).assess(candidate_id)
        assert assessment.bootstrap_promotion is False
        assert assessment.promotion_mode == "replacement"
        assert assessment.promotable is True
        assert assessment.current_active_version_id == str(active_id)
        assert assessment.source_reconciliation_job_id == str(source_id)
        assert assessment.diff_totals is not None
        session.rollback()

        with session.begin():
            result = KnowledgePromotionService(session).promote_replacement(
                candidate_id,
                reviewer="reviewer:test",
                reason="Candidate reconciliado y revisado.",
                expected_knowledge_version=assessment.knowledge_version,
            )

        active = session.get(KnowledgeVersionRecord, active_id)
        candidate = session.get(KnowledgeVersionRecord, candidate_id)
        assert active.status == KnowledgeVersionStatus.ARCHIVED
        assert candidate.status == KnowledgeVersionStatus.ACTIVE
        assert result.previous_active_version_id == str(active_id)
        promotion = session.scalar(
            select(KnowledgeVersionPromotion).where(
                KnowledgeVersionPromotion.knowledge_version_id == candidate_id
            )
        )
        assert promotion.previous_active_version_id == active_id
        jobs = list(
            session.scalars(select(SyncJob).where(SyncJob.knowledge_version_id == candidate_id))
        )
        assert len(jobs) == 2
        assert {job.status for job in jobs} == {SyncStatus.PENDING}

    engine.dispose()


def test_replacement_promotion_flushes_archive_before_activation(tmp_path, monkeypatch):
    from tests.structural.governance.test_version_diff_service import seed_reconciled

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        active_id, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)
        assessment = KnowledgePromotionService(session).assess(candidate_id)
        assert assessment.promotable is True
        session.rollback()

        observed_statuses: list[tuple[KnowledgeVersionStatus, KnowledgeVersionStatus]] = []

        with session.begin():
            active = session.get(KnowledgeVersionRecord, active_id)
            candidate = session.get(KnowledgeVersionRecord, candidate_id)
            assert active is not None
            assert candidate is not None

            original_flush = session.flush

            def tracking_flush(*args, **kwargs):
                observed_statuses.append((active.status, candidate.status))
                return original_flush(*args, **kwargs)

            monkeypatch.setattr(session, "flush", tracking_flush)

            KnowledgePromotionService(session).promote_replacement(
                candidate_id,
                reviewer="reviewer:test",
                reason="Candidate reconciliado y revisado.",
                expected_knowledge_version=assessment.knowledge_version,
            )

        archived_imported = (
            KnowledgeVersionStatus.ARCHIVED,
            KnowledgeVersionStatus.IMPORTED,
        )
        archived_active = (
            KnowledgeVersionStatus.ARCHIVED,
            KnowledgeVersionStatus.ACTIVE,
        )

        assert archived_imported in observed_statuses
        assert archived_active in observed_statuses
        assert observed_statuses.index(archived_imported) < observed_statuses.index(archived_active)

    engine.dispose()


@pytest.mark.parametrize("target", [SyncTarget.NEO4J, SyncTarget.CHROMADB])
def test_replacement_gate_blocks_running_projection_of_current_active(tmp_path, target):
    from tests.structural.governance.test_version_diff_service import seed_reconciled

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        active_id, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)
        with session.begin():
            job = session.scalar(
                select(SyncJob).where(
                    SyncJob.knowledge_version_id == active_id,
                    SyncJob.target == target,
                )
            )
            assert job is not None
            job.status = SyncStatus.RUNNING

        assessment = KnowledgePromotionService(session).assess(candidate_id)

        assert assessment.promotable is False
        blocker = next(
            item for item in assessment.blockers if item.code == "active_projection_sync_running"
        )
        assert blocker.count == 1
        assert target.value in blocker.message

    engine.dispose()


@pytest.mark.parametrize(
    ("kind", "target"),
    [
        (PipelineJobKind.NEO4J_SYNC, SyncTarget.NEO4J),
        (PipelineJobKind.CHROMA_SYNC, SyncTarget.CHROMADB),
    ],
)
def test_replacement_gate_blocks_running_projection_pipeline_job(tmp_path, kind, target):
    from tests.structural.governance.test_version_diff_service import seed_reconciled

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        active_id, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)
        active = session.get(KnowledgeVersionRecord, active_id)
        assert active is not None
        active_knowledge_version = active.knowledge_version
        active_erp_id = active.erp_id
        session.rollback()
        with session.begin():
            session.add(
                PipelineJob(
                    kind=kind,
                    status=PipelineJobStatus.RUNNING,
                    scope=PipelineJobScope.VERSION,
                    target=active_knowledge_version,
                    profile_name="test",
                    erp_id=active_erp_id,
                    knowledge_version_id=active_id,
                    request_source="test",
                    parameters={},
                    stage="syncing",
                    progress_current=1,
                    progress_total=4,
                    checkpoint={},
                    requested_at=datetime.now(timezone.utc),
                    started_at=datetime.now(timezone.utc),
                )
            )

        assessment = KnowledgePromotionService(session).assess(candidate_id)

        assert assessment.promotable is False
        blocker = next(
            item for item in assessment.blockers if item.code == "active_projection_sync_running"
        )
        assert blocker.count == 1
        assert target.value in blocker.message

    engine.dispose()


def test_replacement_gate_blocks_unreviewed_new_or_modified_items(tmp_path):
    from tests.structural.governance.test_version_diff_service import seed_reconciled

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        _, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)
        candidate_item = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == candidate_id)
        )
        candidate_item.content_hash = "a" * 64
        candidate_item.current_review_status = ReviewStatus.PENDING_REVIEW
        session.commit()

        assessment = KnowledgePromotionService(session).assess(candidate_id)
        assert assessment.promotable is False
        assert any(
            blocker.code == "replacement_structural_review_incomplete"
            for blocker in assessment.blockers
        )

    engine.dispose()


def test_bootstrap_gate_blocks_candidate_without_certified_crawl_quality(session, staged):
    _approve_required(session, staged)
    source = session.scalar(
        select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
    )
    source.result_payload = {
        key: value
        for key, value in dict(source.result_payload or {}).items()
        if key != "crawl_execution_quality"
    }

    assessment = KnowledgePromotionService(session).assess(staged)

    assert assessment.promotable is False
    assert any(blocker.code == "crawl_quality_not_certified" for blocker in assessment.blockers)
