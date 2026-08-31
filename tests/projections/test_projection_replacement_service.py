from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.base import Base
from erp_assistant.persistence.postgres.enums import ImportStatus, KnowledgeVersionStatus
from erp_assistant.persistence.postgres.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
)
from erp_assistant.projections.replacement_service import (
    ProjectionReplacementError,
    ProjectionReplacementService,
)


def _version(erp, *, name: str, status: KnowledgeVersionStatus):
    run = ImportRun(
        erp=erp,
        source_knowledge_path=f"{name}.json",
        source_manifest_path=f"{name}-manifest.json",
        requested_knowledge_version=name,
        status=ImportStatus.SUCCEEDED,
        source_hashes={},
    )
    return KnowledgeVersionRecord(
        erp=erp,
        import_run=run,
        schema_version="1.1.0",
        knowledge_version=name,
        canonical_hash=("a" if status == KnowledgeVersionStatus.ARCHIVED else "b") * 64,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=status,
    )


def test_projection_lineage_resolves_previous_archived_active():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        erp = ERPSystemRecord(
            id="erp:replacement",
            slug="replacement",
            name="ERP Replacement",
            profile_name="test",
            safe_metadata={},
        )
        previous = _version(erp, name="v1", status=KnowledgeVersionStatus.ARCHIVED)
        current = _version(erp, name="v2", status=KnowledgeVersionStatus.ACTIVE)
        session.add_all([previous, current])
        session.flush()
        session.add(
            KnowledgeVersionPromotion(
                knowledge_version_id=current.id,
                previous_active_version_id=previous.id,
                reviewer_subject="reviewer:test",
                reason="replacement",
                source="api",
                gate_snapshot={},
            )
        )
        session.commit()

        lineage = ProjectionReplacementService(session).resolve(current.id)
        assert lineage.replacement is True
        assert lineage.previous_active_version_id == previous.id
        assert lineage.previous_active_knowledge_version == "v1"
        assert lineage.knowledge_version == "v2"

    engine.dispose()


def test_projection_lineage_rejects_non_archived_previous_version():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        erp = ERPSystemRecord(
            id="erp:invalid-replacement",
            slug="invalid-replacement",
            name="ERP Invalid Replacement",
            profile_name="test",
            safe_metadata={},
        )
        previous = _version(erp, name="v1", status=KnowledgeVersionStatus.IMPORTED)
        current = _version(erp, name="v2", status=KnowledgeVersionStatus.ACTIVE)
        session.add_all([previous, current])
        session.flush()
        session.add(
            KnowledgeVersionPromotion(
                knowledge_version_id=current.id,
                previous_active_version_id=previous.id,
                reviewer_subject="reviewer:test",
                reason="invalid replacement",
                source="api",
                gate_snapshot={},
            )
        )
        session.commit()

        with pytest.raises(ProjectionReplacementError, match="no está ARCHIVED"):
            ProjectionReplacementService(session).resolve(current.id)

    engine.dispose()
