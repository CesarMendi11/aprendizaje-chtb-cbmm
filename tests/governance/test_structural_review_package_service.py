from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import src.database.models  # noqa: F401
from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import PipelineJobKind, ReviewActionType, ReviewSource
from src.database.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    PipelineJob,
    ReviewAction,
    SyncJob,
)
from src.database.services import StructuralReviewPackageService
from src.database.services.structural_review_package_service import StructuralReviewPackageError
from src.database.services.version_diff_service import VersionDiffChangeType, VersionDiffItem
from tests.governance.test_version_diff_service import seed, seed_reconciled


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def test_packages_group_canonical_children_and_keep_partial_removals_unconfirmed(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    with session.begin():
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        source.kind = PipelineJobKind.CANONICAL_MERGE
        source.result_payload = {**source.result_payload, "merged_from_scope": "module"}

    result = StructuralReviewPackageService(session).build(candidate_id)

    assert result.candidate_origin == "partial_module_merge"
    assert result.unconfirmed_removals == 1
    changed = next(package for package in result.packages if package.review_required)
    assert changed.counts["modified"] >= 1
    assert any(
        change.requires_removal_review for package in result.packages for change in package.changes
    )
    assert all(
        change.removal_confirmation != "confirmed_removed"
        for package in result.packages
        for change in package.changes
    )



def test_full_candidate_removals_are_unconfirmed_and_require_review(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)

    result = StructuralReviewPackageService(session).build(candidate_id)

    assert result.candidate_origin == "full_canonical"
    assert result.unconfirmed_removals == result.diff_totals["removed"] > 0
    removed = [
        change
        for package in result.packages
        for change in package.changes
        if change.change_type == "removed"
    ] + [
        change for change in result.unscoped_changes if change.change_type == "removed"
    ]
    assert len(removed) == result.diff_totals["removed"]
    assert all(
        change.removal_confirmation == "unconfirmed" and change.requires_removal_review
        for change in removed
    )

def test_unowned_change_is_unscoped_never_route_guessed(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    with session.begin():
        item = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == candidate_id)
        )
        item.entity_type = "global_entity"
        item.canonical_id = "global:unowned"
    result = StructuralReviewPackageService(session).build(candidate_id)
    assert any(change.canonical_id == "global:unowned" for change in result.unscoped_changes)


def test_screen_carry_forward_requires_real_review_action(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    service = StructuralReviewPackageService(session)
    before = service.build(candidate_id)
    package = next(item for item in before.packages if item.change_type == "unchanged")
    assert package.carry_forward is False
    candidate = session.get(KnowledgeItem, uuid.UUID(package.candidate_item_id))
    active = session.get(KnowledgeItem, uuid.UUID(package.active_item_id))
    session.add(
        ReviewAction(
            knowledge_item_id=candidate.id,
            previous_item_id=active.id,
            action=ReviewActionType.APPROVE,
            previous_status=candidate.current_review_status,
            new_status=candidate.current_review_status,
            corrected_payload=None,
            review_notes="carry forward fixture",
            item_content_hash=candidate.content_hash,
            source=ReviewSource.CARRY_FORWARD,
        )
    )
    session.flush()
    after = service.build(candidate_id)
    assert (
        next(item for item in after.packages if item.screen_id == package.screen_id).carry_forward
        is True
    )
    changed = next(item for item in after.packages if item.change_type != "unchanged")
    assert changed.carry_forward is None


def test_snapshot_ownership_is_fail_closed_and_module_paths_are_strict(session):
    service = StructuralReviewPackageService(session)
    active_screen = SimpleNamespace(
        entity_type="screen", canonical_id="screen:a", source_payload={}
    )
    candidate_screen = SimpleNamespace(
        entity_type="screen", canonical_id="screen:a", source_payload={}
    )
    candidate_screen_b = SimpleNamespace(
        entity_type="screen", canonical_id="screen:b", source_payload={}
    )
    active_field = SimpleNamespace(
        entity_type="field", canonical_id="field:x", source_payload={"screen_id": "screen:a"}
    )
    candidate_field = SimpleNamespace(
        entity_type="field", canonical_id="field:x", source_payload={"screen_id": "screen:b"}
    )
    active_items = {("screen", "screen:a"): active_screen, ("field", "field:x"): active_field}
    candidate_items = {
        ("screen", "screen:a"): candidate_screen,
        ("screen", "screen:b"): candidate_screen_b,
        ("field", "field:x"): candidate_field,
    }
    modified = VersionDiffItem(
        VersionDiffChangeType.MODIFIED,
        "field",
        "field:x",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert service._screen_owner(modified, active_items, candidate_items) is None
    new = VersionDiffItem(
        VersionDiffChangeType.NEW,
        "field",
        "field:x",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert service._screen_owner(new, active_items, candidate_items) == "screen:b"
    modules = {
        ("module", "module:root"): SimpleNamespace(source_payload={"parent_module_id": None}),
        ("module", "module:child"): SimpleNamespace(
            source_payload={"parent_module_id": "module:root"}
        ),
    }
    assert service._module_path("module:child", modules) == ("module:root", "module:child")
    with pytest.raises(StructuralReviewPackageError, match="inexistente"):
        service._module_path("module:missing", modules)
    modules[("module", "module:root")].source_payload["parent_module_id"] = "module:child"
    with pytest.raises(StructuralReviewPackageError, match="ciclo"):
        service._module_path("module:child", modules)


def test_transition_and_typed_evidence_ownership_are_fail_closed(session):
    service = StructuralReviewPackageService(session)
    screen_a = SimpleNamespace(entity_type="screen", canonical_id="screen:a", source_payload={})
    screen_b = SimpleNamespace(entity_type="screen", canonical_id="screen:b", source_payload={})
    state_a1 = SimpleNamespace(
        entity_type="ui_state", canonical_id="state:a1", source_payload={"screen_id": "screen:a"}
    )
    state_a2 = SimpleNamespace(
        entity_type="ui_state", canonical_id="state:a2", source_payload={"screen_id": "screen:a"}
    )
    state_b = SimpleNamespace(
        entity_type="ui_state", canonical_id="state:b", source_payload={"screen_id": "screen:b"}
    )
    table = SimpleNamespace(
        entity_type="table", canonical_id="table:a", source_payload={"screen_id": "screen:a"}
    )
    items = {
        ("screen", "screen:a"): screen_a,
        ("screen", "screen:b"): screen_b,
        ("ui_state", "state:a1"): state_a1,
        ("ui_state", "state:a2"): state_a2,
        ("ui_state", "state:b"): state_b,
        ("table", "table:a"): table,
    }
    same = SimpleNamespace(
        entity_type="transition",
        source_payload={"source_state_id": "state:a1", "target_state_id": "state:a2"},
    )
    cross = SimpleNamespace(
        entity_type="transition",
        source_payload={"source_state_id": "state:a1", "target_state_id": "state:b"},
    )
    missing_target = SimpleNamespace(
        entity_type="transition",
        source_payload={"source_state_id": "state:a1", "target_state_id": "state:missing"},
    )
    missing_source = SimpleNamespace(
        entity_type="transition",
        source_payload={"source_state_id": "state:missing", "target_state_id": "state:a2"},
    )
    assert service._owner_for_item(same, items) == "screen:a"
    assert service._owner_for_item(cross, items) is None
    assert service._owner_for_item(missing_target, items) is None
    assert service._owner_for_item(missing_source, items) is None
    evidence_screen = SimpleNamespace(
        entity_type="evidence",
        source_payload={"source_entity_type": "screen", "source_entity_id": "screen:a"},
    )
    evidence_table = SimpleNamespace(
        entity_type="evidence",
        source_payload={"source_entity_type": "table", "source_entity_id": "table:a"},
    )
    wrong_type = SimpleNamespace(
        entity_type="evidence",
        source_payload={"source_entity_type": "field", "source_entity_id": "table:a"},
    )
    missing_id = SimpleNamespace(
        entity_type="evidence",
        source_payload={"source_entity_type": "table", "source_entity_id": "table:missing"},
    )
    assert service._owner_for_item(evidence_screen, items) == "screen:a"
    assert service._owner_for_item(evidence_table, items) == "screen:a"
    assert service._owner_for_item(wrong_type, items) is None
    assert service._owner_for_item(missing_id, items) is None


def test_package_summary_filters_pagination_and_read_only(session, tmp_path):
    _, candidate_id, _ = seed(session, tmp_path)
    before = {
        "versions": [
            (item.id, item.status) for item in session.scalars(select(KnowledgeVersionRecord))
        ],
        "items": [
            (item.id, item.content_hash, item.current_review_status, item.review_revision)
            for item in session.scalars(select(KnowledgeItem))
        ],
        "actions": session.query(ReviewAction).count(),
        "pipeline": [(item.id, item.status) for item in session.scalars(select(PipelineJob))],
        "sync": [(item.id, item.status) for item in session.scalars(select(SyncJob))],
    }
    service = StructuralReviewPackageService(session)
    full = service.build(candidate_id)
    page_one = service.build(candidate_id, limit=1, offset=0)
    page_two = service.build(candidate_id, limit=1, offset=1)
    changed = service.build(candidate_id, changed_only=True)
    module_id = next(item.module_id for item in full.packages if item.module_id is not None)
    module = service.build(candidate_id, module_id=module_id)
    after = {
        "versions": [
            (item.id, item.status) for item in session.scalars(select(KnowledgeVersionRecord))
        ],
        "items": [
            (item.id, item.content_hash, item.current_review_status, item.review_revision)
            for item in session.scalars(select(KnowledgeItem))
        ],
        "actions": session.query(ReviewAction).count(),
        "pipeline": [(item.id, item.status) for item in session.scalars(select(PipelineJob))],
        "sync": [(item.id, item.status) for item in session.scalars(select(SyncJob))],
    }
    assert before == after
    assert full.diff_totals == service.build(candidate_id).diff_totals
    assert full.affected_screens == len(full.packages)
    assert full.screens_with_changes + full.screens_unchanged == full.affected_screens
    assert all(item.review_required for item in changed.packages)
    assert all(item.module_id == module_id for item in module.packages)
    assert [item.screen_id for item in full.packages] == sorted(
        item.screen_id for item in full.packages
    )
    assert page_one.diff_totals == full.diff_totals == page_two.diff_totals
    assert [item.screen_id for item in (*page_one.packages, *page_two.packages)] == [
        item.screen_id for item in full.packages[:2]
    ]


def test_review_package_api_success_404_and_invalid_provenance(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'review.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _, candidate_id, _ = seed(session, tmp_path)
    app = create_app(
        replace(ApiSettings(), semantic_review_api_enabled=True),
        semantic_review_session_factory=factory,
    )

    async def request(path):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            base_url="http://test",
        ) as client:
            return await client.get(path)

    response = asyncio.run(
        request(f"/api/admin/knowledge-versions/{candidate_id}/review-packages?changed_only=true")
    )
    assert response.status_code == 200, response.text
    assert response.json()["packages"]
    module_id = next(item["module_id"] for item in response.json()["packages"] if item["module_id"])
    filtered = asyncio.run(
        request(
            f"/api/admin/knowledge-versions/{candidate_id}/review-packages"
            f"?changed_only=true&module_id={module_id}&limit=1&offset=0"
        )
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["limit"] == 1
    assert filtered.json()["offset"] == 0
    assert all(item["module_id"] == module_id for item in filtered.json()["packages"])
    missing = asyncio.run(request(f"/api/admin/knowledge-versions/{uuid.uuid4()}/review-packages"))
    assert missing.status_code == 404
    with factory.begin() as session:
        source = session.scalar(
            select(PipelineJob).where(PipelineJob.kind == PipelineJobKind.CANONICAL_BUILD)
        )
        source.result_payload = {**source.result_payload, "snapshot_mode": "partial"}
    invalid = asyncio.run(request(f"/api/admin/knowledge-versions/{candidate_id}/review-packages"))
    assert invalid.status_code == 422
    assert invalid.json()["category"] == "invalid_structural_review_package"
    engine.dispose()


def test_review_package_accepts_reconciled_full_candidate(session, tmp_path):
    _, _, candidate_id, _, _ = seed_reconciled(session, tmp_path)

    result = StructuralReviewPackageService(session).build(candidate_id)

    assert result.candidate_origin == "reconciled_full"
    assert result.diff_totals["removed"] == 0
    assert result.unconfirmed_removals == 0
