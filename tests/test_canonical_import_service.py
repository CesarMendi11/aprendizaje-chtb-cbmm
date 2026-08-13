from __future__ import annotations

import uuid
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.database.base import Base
import src.database.models  # noqa: F401
from src.database.enums import ImportStatus, KnowledgeVersionStatus, SyncStatus
from src.database.models import ImportRun, KnowledgeItem, KnowledgeVersionRecord, SyncJob
from src.database.services import CanonicalImportService
from src.database.services.payloads import item_content_hash
from src.database.services import KnowledgeReviewService, EffectiveKnowledgeService
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.enums import ReviewStatus
from tests.canonical_fixtures import exported_fictional_canonical


@pytest.fixture
def canonical_dir(tmp_path):
    return exported_fictional_canonical(tmp_path / "canonical")


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def import_once(session, canonical_dir):
    with session.begin():
        return CanonicalImportService(session).import_canonical(
            canonical_dir / "knowledge.json",
            canonical_dir / "manifest.json",
            canonical_dir / "build_report.json",
        )


def test_content_hash_is_deterministic_and_ignores_review_metadata():
    a = {"id": "screen:1", "title": "A", "reviewed_at": "today", "nested": {"b": 2, "a": 1}}
    b = {"nested": {"a": 1, "b": 2}, "title": "A", "id": "screen:1", "reviewed_at": "tomorrow"}
    assert item_content_hash(a) == item_content_hash(b)
    assert item_content_hash(a) != item_content_hash({**a, "title": "B"})


def test_new_import_and_idempotency(session, canonical_dir):
    manifest = json.loads(
        (canonical_dir / "manifest.json").read_text(encoding="utf-8")
    )
    expected_items = 1 + sum(manifest["entity_counts"].values())

    first = import_once(session, canonical_dir)
    assert first.result == "imported"
    assert first.items == expected_items
    assert (
        session.scalar(select(func.count()).select_from(KnowledgeItem))
        == expected_items
    )
    assert session.scalar(select(func.count()).select_from(SyncJob)) == 2
    session.rollback()
    second = import_once(session, canonical_dir)
    assert second.result == "skipped"
    assert session.scalar(select(func.count()).select_from(KnowledgeVersionRecord)) == 1
    assert session.scalar(select(func.count()).select_from(ImportRun)) == 2


def test_import_activates_version_and_creates_pending_jobs(session, canonical_dir):
    result = import_once(session, canonical_dir)
    version = session.get(KnowledgeVersionRecord, uuid.UUID(result.version_id))
    assert version.status == KnowledgeVersionStatus.ACTIVE
    assert {job.status for job in version.sync_jobs} == {SyncStatus.PENDING}


def test_dry_run_does_not_write(session, canonical_dir):
    result = CanonicalImportService(session).dry_run(
        canonical_dir / "knowledge.json", canonical_dir / "manifest.json"
    )
    assert result.result == "dry_run"
    assert session.scalar(select(func.count()).select_from(ImportRun)) == 0


def test_invalid_manifest_rolls_back_functional_import(session, tmp_path, canonical_dir):
    bad = tmp_path / "manifest.json"
    bad.write_text('{"knowledge_version":"wrong","canonical_document_hash":"x"}')
    with pytest.raises(ValueError):
        with session.begin():
            CanonicalImportService(session).import_canonical(
                canonical_dir / "knowledge.json", bad
            )
    assert session.scalar(select(func.count()).select_from(KnowledgeItem)) == 0


def test_sensitive_canonical_is_rejected_before_writing(session, tmp_path, canonical_dir):
    knowledge = json.loads((canonical_dir / "knowledge.json").read_text())
    manifest = json.loads((canonical_dir / "manifest.json").read_text())
    knowledge["screens"][0]["main_content_text"] = "001-001-000000001"
    manifest["canonical_document_hash"] = content_hash(knowledge)
    knowledge_path = tmp_path / "knowledge.json"
    manifest_path = tmp_path / "manifest.json"
    knowledge_path.write_text(json.dumps(knowledge))
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="canónico inválido"):
        with session.begin():
            CanonicalImportService(session).import_canonical(knowledge_path, manifest_path)
    assert session.scalar(select(func.count()).select_from(KnowledgeItem)) == 0
    assert session.scalar(select(func.count()).select_from(ImportRun)) == 0


def _next_version(tmp_path, canonical_dir, *, change_screen=False):
    knowledge = json.loads((canonical_dir / "knowledge.json").read_text())
    manifest = json.loads((canonical_dir / "manifest.json").read_text())
    knowledge["knowledge_version"] = "next-version"
    manifest["knowledge_version"] = "next-version"
    if change_screen:
        knowledge["screens"][0]["title"] += " cambiado"
    manifest["canonical_document_hash"] = content_hash(knowledge)
    knowledge_path = tmp_path / "knowledge.json"
    manifest_path = tmp_path / "manifest.json"
    knowledge_path.write_text(json.dumps(knowledge))
    manifest_path.write_text(json.dumps(manifest))
    return knowledge_path, manifest_path


def test_identical_approval_is_carried_forward(session, tmp_path, canonical_dir):
    first = import_once(session, canonical_dir)
    item = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "screen").limit(1))
    session.rollback()
    with session.begin():
        KnowledgeReviewService(session).approve(item.id)
    paths = _next_version(tmp_path, canonical_dir)
    with session.begin():
        result = CanonicalImportService(session).import_canonical(*paths)
    carried = session.scalar(select(KnowledgeItem).where(
        KnowledgeItem.knowledge_version_id == uuid.UUID(result.version_id),
        KnowledgeItem.entity_type == item.entity_type,
        KnowledgeItem.canonical_id == item.canonical_id,
    ))
    assert carried.current_review_status == ReviewStatus.APPROVED
    assert result.carried_reviews == 1


def test_identical_correction_is_carried_forward(session, tmp_path, canonical_dir):
    import_once(session, canonical_dir)
    item = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "screen").limit(1))
    payload = {
        key: value for key, value in item.source_payload.items()
        if key not in {"review_status", "reviewed_at", "reviewed_by", "review_notes"}
    }
    payload["description"] = "Revisión humana"
    session.rollback()
    with session.begin():
        KnowledgeReviewService(session).correct(item.id, payload, notes="ajuste")
    paths = _next_version(tmp_path, canonical_dir)
    with session.begin():
        result = CanonicalImportService(session).import_canonical(*paths)
    carried = session.scalar(select(KnowledgeItem).where(
        KnowledgeItem.knowledge_version_id == uuid.UUID(result.version_id),
        KnowledgeItem.entity_type == item.entity_type,
        KnowledgeItem.canonical_id == item.canonical_id,
    ))
    effective = EffectiveKnowledgeService(session).describe(carried.id)
    assert carried.current_review_status == ReviewStatus.CORRECTED
    assert effective["effective_payload"]["description"] == "Revisión humana"


def test_changed_hash_does_not_carry_review(session, tmp_path, canonical_dir):
    import_once(session, canonical_dir)
    item = session.scalar(select(KnowledgeItem).where(KnowledgeItem.entity_type == "screen").limit(1))
    session.rollback()
    with session.begin():
        KnowledgeReviewService(session).approve(item.id)
    paths = _next_version(tmp_path, canonical_dir, change_screen=True)
    with session.begin():
        result = CanonicalImportService(session).import_canonical(*paths)
    changed = session.scalar(select(KnowledgeItem).where(
        KnowledgeItem.knowledge_version_id == uuid.UUID(result.version_id),
        KnowledgeItem.entity_type == item.entity_type,
        KnowledgeItem.canonical_id == item.canonical_id,
    ))
    assert changed.current_review_status == ReviewStatus.PENDING_REVIEW
    assert result.carried_reviews == 0


def test_imported_child_module_keeps_canonical_module_parent(session, tmp_path, canonical_dir):
    knowledge = json.loads(
        (canonical_dir / "knowledge.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (canonical_dir / "manifest.json").read_text(encoding="utf-8")
    )

    assert len(knowledge["modules"]) >= 2

    # Normalize the fixture to the new hierarchy contract.
    knowledge["schema_version"] = "1.1.0"
    knowledge["knowledge_version"] = "recursive-module-parent-test"

    for module in knowledge["modules"]:
        module["parent_module_id"] = None
        module["depth"] = 0
        module["navigation_path"] = [module["name"]]

    parent = knowledge["modules"][0]
    child = knowledge["modules"][1]

    child["parent_module_id"] = parent["id"]
    child["depth"] = 1
    child["navigation_path"] = [
        parent["name"],
        child["name"],
    ]

    manifest["schema_version"] = "1.1.0"
    manifest["knowledge_version"] = knowledge["knowledge_version"]
    manifest["canonical_document_hash"] = content_hash(knowledge)

    knowledge_path = tmp_path / "knowledge.json"
    manifest_path = tmp_path / "manifest.json"

    knowledge_path.write_text(
        json.dumps(knowledge, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    with session.begin():
        result = CanonicalImportService(session).import_canonical(
            knowledge_path,
            manifest_path,
            activate=False,
            create_sync_jobs=False,
        )

    imported_child = session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == uuid.UUID(result.version_id),
            KnowledgeItem.entity_type == "module",
            KnowledgeItem.canonical_id == child["id"],
        )
    )

    assert imported_child is not None
    assert imported_child.parent_canonical_id == parent["id"]
