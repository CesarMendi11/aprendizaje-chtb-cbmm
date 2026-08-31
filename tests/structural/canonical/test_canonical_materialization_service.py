from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from erp_assistant.persistence.postgres.base import Base
from erp_assistant.structural.services.canonical_import_service import CanonicalImportService
from erp_assistant.structural.services.canonical_materialization_service import (
    CanonicalKnowledgeMaterializer,
)
from erp_assistant.structural.canonical import CanonicalKnowledgeExporter, CanonicalSnapshotContext
from erp_assistant.structural.canonical.models import CanonicalKnowledgeBase


def _knowledge() -> CanonicalKnowledgeBase:
    return CanonicalKnowledgeBase.model_validate(
        {
            "schema_version": "1.1.0",
            "knowledge_version": "base-v1",
            "generated_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
            "generator_version": "test",
            "source_profile": "test",
            "source_artifacts": ["screen_index.json"],
            "source_artifact_hashes": {"screen_index.json": "hash-screen-index"},
            "erp_system": {
                "id": "erp:test",
                "slug": "test",
                "name": "Test ERP",
                "profile_name": "test",
            },
            "modules": [
                {
                    "id": "module:sales",
                    "erp_id": "erp:test",
                    "parent_module_id": None,
                    "depth": 0,
                    "navigation_path": ["Sales"],
                    "name": "Sales",
                    "normalized_name": "sales",
                }
            ],
            "screens": [
                {
                    "id": "screen:orders",
                    "erp_id": "erp:test",
                    "module_id": "module:sales",
                    "title": "Orders",
                    "normalized_title": "orders",
                    "route": "/orders",
                }
            ],
            "statistics": {
                "modules": 1,
                "screens": 1,
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


def test_materializes_full_generated_canonical_from_postgresql(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    source = _knowledge()
    bundle = tmp_path / "base"
    CanonicalKnowledgeExporter().export(
        source,
        bundle,
        snapshot_context=CanonicalSnapshotContext.full(),
    )
    with factory.begin() as session:
        imported = CanonicalImportService(session).import_canonical(
            bundle / "knowledge.json",
            bundle / "manifest.json",
            bundle / "build_report.json",
            activate=True,
            create_sync_jobs=False,
        )
        version_id = imported.version_id

    with factory() as session:
        materialized = CanonicalKnowledgeMaterializer(session).materialize(
            version_id,
            require_active=True,
        )

    assert materialized.knowledge_version == source.knowledge_version
    assert materialized.schema_version == source.schema_version
    assert materialized.erp_system == source.erp_system
    assert [item.model_dump(mode="json") for item in materialized.modules] == [
        item.model_dump(mode="json") for item in source.modules
    ]
    assert [item.model_dump(mode="json") for item in materialized.screens] == [
        item.model_dump(mode="json") for item in source.screens
    ]
    assert materialized.source_profile == "test"
    assert materialized.source_artifacts == ["screen_index.json"]
    assert materialized.statistics["modules"] == 1
    assert materialized.statistics["screens"] == 1
    engine.dispose()


def test_materialization_recovers_exact_profile_path_from_persisted_fingerprint(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    source = _knowledge().model_copy(
        update={
            "source_profile": "configs/test.yaml",
            "source_artifacts": [
                "profile:configs/test.yaml",
                "screen_index.json",
            ],
            "source_artifact_hashes": {
                "profile:configs/test.yaml": "a" * 64,
                "screen_index.json": "hash-screen-index",
            },
        }
    )
    bundle = tmp_path / "profile-provenance"
    CanonicalKnowledgeExporter().export(
        source,
        bundle,
        snapshot_context=CanonicalSnapshotContext.full(),
    )
    with factory.begin() as session:
        imported = CanonicalImportService(session).import_canonical(
            bundle / "knowledge.json",
            bundle / "manifest.json",
            bundle / "build_report.json",
            activate=True,
            create_sync_jobs=False,
        )
        version_id = imported.version_id

    with factory() as session:
        materialized = CanonicalKnowledgeMaterializer(session).materialize(
            version_id,
            require_active=True,
        )

    assert materialized.source_profile == "configs/test.yaml"
    assert (
        materialized.source_artifact_hashes["profile:configs/test.yaml"]
        == "a" * 64
    )
    engine.dispose()


def test_materializer_recovers_base_profile_from_merged_provenance():
    hashes = {
        "base:base:profile:configs/base.yaml": "a" * 64,
        "base:partial:profile:configs/older-partial.yaml": "b" * 64,
        "partial:profile:configs/new-partial.yaml": "c" * 64,
    }

    assert (
        CanonicalKnowledgeMaterializer._source_profile(hashes)
        == "configs/base.yaml"
    )
