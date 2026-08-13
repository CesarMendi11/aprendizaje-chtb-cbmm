from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.database.base import Base
from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    SyncStatus,
)
from src.database.models import (
    KnowledgeItem,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
    PipelineJob,
    SyncJob,
)
from src.database.services import (
    CanonicalImportService,
    KnowledgePromotionBlockedError,
    KnowledgePromotionService,
    KnowledgeReviewService,
)
from tests.canonical_fixtures import exported_fictional_canonical


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
        source = PipelineJob(
            kind=PipelineJobKind.CANONICAL_BUILD,
            status=PipelineJobStatus.SUCCEEDED,
            scope=PipelineJobScope.FULL,
            target=None,
            profile_name="synthetic",
            request_source="admin_api",
            parameters={"source_crawl_job_id": str(uuid.uuid4())},
            stage="completed",
            progress_current=4,
            progress_total=4,
            checkpoint={"knowledge_version": version.knowledge_version},
            result_payload={
                "snapshot_mode": "full",
                "snapshot_scope": "full",
                "knowledge_version": version.knowledge_version,
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
