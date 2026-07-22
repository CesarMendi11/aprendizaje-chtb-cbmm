from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import sessionmaker
from test_semantic_review_api import Client

from src.api.admin_knowledge_serializers import semantic_projection
from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus, SemanticType
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    ReviewAction,
    SemanticProposal,
    SemanticReviewAction,
)
from src.knowledge.canonical.enums import ReviewStatus

HASH = "a" * 64


@pytest.fixture
def admin_api(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    database = tmp_path / "admin.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True)
    yield Client(create_app(settings, semantic_review_session_factory=factory)), factory, engine
    engine.dispose()


def _module(identifier: str, name: str) -> dict:
    return {
        "id": identifier,
        "erp_id": "erp:tree",
        "name": name,
        "normalized_name": name.casefold(),
        "route_prefix": f"/{name.casefold()}",
        "evidence_ids": ["evidence:module"],
        "review_status": "approved",
    }


def _screen(identifier: str, title: str, module_id: str | None) -> dict:
    return {
        "id": identifier,
        "erp_id": "erp:tree",
        "module_id": module_id,
        "title": title,
        "normalized_title": title.casefold(),
        "route": f"/{title.casefold()}",
        "evidence_ids": ["evidence:one"],
        "review_status": "approved",
    }


def _add_item(session, version, payload, entity_type, status=ReviewStatus.APPROVED):
    title = payload.get("title") or payload.get("name") or payload.get("label")
    normalized_title = (
        payload.get("normalized_title")
        or payload.get("normalized_name")
        or payload.get("normalized_label")
    )
    item = KnowledgeItem(
        knowledge_version=version,
        canonical_id=payload["id"],
        entity_type=entity_type,
        parent_canonical_id=payload.get("module_id"),
        title=title if isinstance(title, str) else None,
        normalized_title=(normalized_title if isinstance(normalized_title, str) else None),
        route=payload.get("route") or payload.get("route_prefix"),
        content_hash=HASH,
        source_payload=payload,
        generated_review_status=status,
        current_review_status=status,
    )
    session.add(item)
    session.flush()
    return item


def _proposal(session, version, screen, status=ReviewStatus.PENDING_REVIEW, *, invalid=False):
    source = {
        "semantic_type": "screen_purpose",
        "screen_id": screen.canonical_id,
        "purpose_summary": "Permite consultar datos.",
        "supported_capabilities": [
            {"statement": "Permite consultar datos.", "evidence_refs": ["control:buscar"]}
        ],
        "limitations": [],
        "uncertainties": [],
    }
    evidence = {
        "schema_version": "1.0",
        "erp_id": "erp:tree",
        "knowledge_version_id": str(version.id),
        "knowledge_version": version.knowledge_version,
        "screen_id": screen.canonical_id,
        "screen_title": screen.title,
        "screen_route": screen.route,
        "module": {"module_id": "module:a", "name": "Alpha"},
        "fields": [],
        "controls": [
            {
                "control_id": "control:buscar",
                "label": "Buscar",
                "control_type": "button",
                "mutative": False,
                "safety_decision": "allow",
            }
        ],
        "tables": [],
        "ui_states": [],
        "events": [],
        "transitions": [],
        "main_content_text": "Consulta segura",
        "evidence_ids": ["evidence:module", "evidence:one"],
        "warnings": ["synthetic warning"],
    }
    proposal = SemanticProposal(
        semantic_id=f"semantic:{screen.canonical_id}:{status}",
        knowledge_version=version,
        screen_knowledge_item=screen,
        semantic_type=SemanticType.SCREEN_PURPOSE,
        source_payload={"invalid": True} if invalid else source,
        source_content_hash=HASH,
        evidence_payload={"invalid": True} if invalid else evidence,
        evidence_hash="b" * 64,
        evidence_ids=["evidence:module", "evidence:one"],
        generation_model="synthetic",
        prompt_version="v1",
        prompt_hash="c" * 64,
        generation_parameters={},
        generation_parameters_hash="d" * 64,
        current_review_status=status,
    )
    session.add(proposal)
    session.flush()
    return proposal


def seed_tree(factory):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:tree", slug="tree", name="Tree ERP", profile_name="test", safe_metadata={}
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="synthetic.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="v1",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.0",
            knowledge_version="v1",
            canonical_hash=HASH,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        module_b = _add_item(session, version, _module("module:b", "Beta"), "module")
        module_a = _add_item(session, version, _module("module:a", "Alpha"), "module")
        first = _add_item(
            session,
            version,
            _screen("screen:z", "Zulu", module_a.canonical_id),
            "screen",
        )
        second = _add_item(
            session,
            version,
            _screen("screen:a", "Alpha", module_a.canonical_id),
            "screen",
        )
        _add_item(
            session,
            version,
            {
                "id": "control:buscar",
                "screen_id": second.canonical_id,
                "label": "Buscar",
                "normalized_label": "buscar",
                "control_type": "button",
                "mutative": False,
                "safety_decision": "allow",
                "evidence_ids": [],
                "review_status": "approved",
            },
            "control",
        )
        unassigned = _add_item(session, version, _screen("screen:u", "Loose", None), "screen")
        pending = _add_item(
            session,
            version,
            _screen("screen:pending", "Pending", module_b.canonical_id),
            "screen",
            ReviewStatus.PENDING_REVIEW,
        )
        _proposal(session, version, first)
        approved = _proposal(session, version, second, ReviewStatus.APPROVED)
        invalid = _proposal(session, version, unassigned, invalid=True)
        session.add(
            ReviewAction(
                knowledge_item_id=unassigned.id,
                action="correct",
                previous_status=ReviewStatus.APPROVED,
                new_status=ReviewStatus.CORRECTED,
                corrected_payload=_screen("screen:u", "Corrected Loose", module_b.canonical_id),
                item_content_hash=HASH,
                source="api",
            )
        )
        unassigned.current_review_status = ReviewStatus.CORRECTED
        session.add(
            SemanticReviewAction(
                semantic_proposal_id=approved.id,
                action="approve",
                previous_status=ReviewStatus.PENDING_REVIEW,
                new_status=ReviewStatus.APPROVED,
                reviewer_subject="reviewer:test",
                review_notes="Synthetic review",
                proposal_content_hash=HASH,
                source="admin_api",
            )
        )
        return {
            "version_id": str(version.id),
            "first": first.canonical_id,
            "second": second.canonical_id,
            "pending": pending.canonical_id,
            "invalid": invalid.semantic_id,
        }


def test_tree_disabled_and_remote_blocked(tmp_path, admin_api):
    index = tmp_path / "disabled.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    disabled = Client(
        create_app(replace(ApiSettings(), screen_index_path=index)),
    )
    assert disabled.get("/api/admin/knowledge-tree").status_code == 404
    assert "/api/admin/knowledge-tree" not in disabled.get("/openapi.json").json()["paths"]
    client, _, _ = admin_api
    remote = Client(client.app, client=("192.0.2.1", 1234))
    assert remote.get("/api/admin/knowledge-tree").status_code == 404


def test_empty_tree(admin_api):
    client, _, _ = admin_api
    assert client.get("/api/admin/knowledge-tree").json() == {"erps": []}


def test_tree_hierarchy_corrections_order_states_counters_and_filters(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    response = client.get("/api/admin/knowledge-tree?include_empty_modules=true")
    assert response.status_code == 200, response.text
    erp = response.json()["erps"][0]
    assert [module["name"] for module in erp["modules"]] == ["Alpha", "Beta"]
    assert [screen["title"] for screen in erp["modules"][0]["screens"]] == ["Alpha", "Zulu"]
    assert erp["modules"][1]["screens"][0]["title"] == "Corrected Loose"
    assert erp["unassigned_screens"] == []
    assert seeded["pending"] not in str(erp)
    assert erp["counters"] == {
        "total_screens": 3,
        "no_proposal": 0,
        "pending_review": 1,
        "approved": 1,
        "corrected": 0,
        "rejected": 0,
        "unavailable": 1,
        "warnings_total": 2,
    }
    filtered = client.get("/api/admin/knowledge-tree?semantic_status=pending_review&search=zulu")
    assert filtered.json()["erps"][0]["counters"]["total_screens"] == 1
    assert client.get("/api/admin/knowledge-tree?semantic_status=bad").status_code == 422
    assert client.get("/api/admin/knowledge-tree?knowledge_version_id=bad").status_code == 422


def test_screen_list_pagination_and_review_context(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    page = client.get("/api/admin/screens?limit=1&offset=1")
    assert page.status_code == 200
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 1
    context = client.get(f"/api/admin/screens/{seeded['second']}/review-context")
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["erp"]["erp_id"] == "erp:tree"
    assert body["module"]["module_id"] == "module:a"
    assert body["semantic_state"] == "approved"
    assert body["structural_evidence"]["evidence_available"] is True
    assert body["traceability"]["review_action_count"] == 1
    assert body["semantic_proposals"][0]["evidence_matches_current_structure"] is True
    assert body["navigation"] == {
        "previous_screen_id": None,
        "next_screen_id": seeded["first"],
        "module_screen_position": 1,
        "module_screen_total": 2,
    }
    serialized = context.text.casefold()
    assert all(term not in serialized for term in ("raw_response", "cookie", "selector"))
    assert client.get("/api/admin/screens/screen:missing/review-context").status_code == 404


def test_comparable_structure_matches_naturally_and_ignores_warnings(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    first = client.get(f"/api/admin/screens/{seeded['second']}/review-context").json()
    proposal_context = first["semantic_proposals"][0]
    assert proposal_context["evidence_matches_current_structure"] is True
    assert (
        proposal_context["historical_structure_hash"] == proposal_context["current_structure_hash"]
    )
    with factory.begin() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(
                SemanticProposal.screen_knowledge_item.has(
                    KnowledgeItem.canonical_id == seeded["second"]
                )
            )
        )
        changed_evidence = {**proposal.evidence_payload, "warnings": ["another warning"]}
        session.execute(
            update(SemanticProposal)
            .where(SemanticProposal.id == proposal.id)
            .values(evidence_payload=changed_evidence)
        )
    second = client.get(f"/api/admin/screens/{seeded['second']}/review-context").json()
    assert (
        second["semantic_proposals"][0]["historical_structure_hash"]
        == proposal_context["historical_structure_hash"]
    )
    with factory.begin() as session:
        screen = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == seeded["second"])
        )
        corrected = {
            **screen.source_payload,
            "title": "Changed title",
            "normalized_title": "changed title",
        }
        session.add(
            ReviewAction(
                knowledge_item_id=screen.id,
                action="correct",
                previous_status=ReviewStatus.APPROVED,
                new_status=ReviewStatus.CORRECTED,
                corrected_payload=corrected,
                item_content_hash=HASH,
                source="api",
            )
        )
        screen.current_review_status = ReviewStatus.CORRECTED
    changed = client.get(f"/api/admin/screens/{seeded['second']}/review-context").json()
    assert changed["semantic_proposals"][0]["evidence_matches_current_structure"] is False


def test_read_endpoints_do_not_write_and_have_bounded_queries(admin_api):
    client, factory, engine = admin_api
    seed_tree(factory)
    before = {}
    with factory() as session:
        for model in (ReviewAction, SemanticProposal, SemanticReviewAction):
            before[model] = session.scalar(select(func.count()).select_from(model))
    statements = []

    def record(*args):
        statements.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get("/api/admin/knowledge-tree").status_code == 200
        assert client.get("/api/admin/screens?limit=1").status_code == 200
        assert client.get("/api/admin/screens/screen:a/review-context").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) <= 30
    assert not any(
        statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        for statement in statements
    )
    with factory() as session:
        for model, count in before.items():
            assert session.scalar(select(func.count()).select_from(model)) == count


def test_query_count_is_stable_with_multiple_erps(admin_api):
    client, factory, engine = admin_api
    seed_tree(factory)
    with factory.begin() as session:
        for index in range(8):
            erp = ERPSystemRecord(
                id=f"erp:many:{index}",
                slug=f"many-{index}",
                name=f"ERP {index}",
                profile_name="test",
                safe_metadata={},
            )
            run = ImportRun(
                erp=erp,
                source_knowledge_path="synthetic.json",
                source_manifest_path="manifest.json",
                requested_knowledge_version=f"many-{index}",
                status=ImportStatus.SUCCEEDED,
                source_hashes={},
            )
            version = KnowledgeVersionRecord(
                erp=erp,
                import_run=run,
                schema_version="1.0",
                knowledge_version=f"many-{index}",
                canonical_hash=HASH,
                generated_at=datetime.now(timezone.utc),
                entity_counts={},
                source_artifact_hashes={},
                build_warnings=[],
                status=KnowledgeVersionStatus.ACTIVE,
            )
            payload = {
                **_screen(f"screen:many:{index}", f"Many {index}", None),
                "erp_id": erp.id,
            }
            _add_item(session, version, payload, "screen")
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/api/admin/knowledge-tree")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(response.json()["erps"]) == 9
    assert len(statements) <= 5


def test_version_and_erp_errors_are_sanitized(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    assert client.get("/api/admin/knowledge-tree?erp_id=missing").status_code == 404
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(seeded["version_id"]))
        version.status = KnowledgeVersionStatus.ARCHIVED
    response = client.get(f"/api/admin/knowledge-tree?knowledge_version_id={seeded['version_id']}")
    assert response.status_code == 409
    assert "SELECT" not in response.text


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ReviewStatus.PENDING_REVIEW, "pending_review"),
        (ReviewStatus.APPROVED, "approved"),
        (ReviewStatus.REJECTED, "rejected"),
    ],
)
def test_independent_persisted_semantic_states(admin_api, status, expected):
    _, factory, _ = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        screen = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == seeded["first"])
        )
        proposal = session.scalar(
            select(SemanticProposal).where(SemanticProposal.screen_knowledge_item_id == screen.id)
        )
        proposal.current_review_status = status
    with factory() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(SemanticProposal.screen_knowledge_item_id == screen.id)
        )
        assert semantic_projection(((proposal, 0),)).state == expected


def test_no_proposal_mixed_and_unavailable_states(admin_api):
    assert semantic_projection(()).state == "no_proposal"
    _, factory, _ = admin_api
    seed_tree(factory)
    with factory() as session:
        invalid = session.scalar(
            select(SemanticProposal).where(SemanticProposal.source_payload == {"invalid": True})
        )
        assert semantic_projection(((invalid, 0),)).state == "unavailable"
        first = session.scalars(select(SemanticProposal).limit(2)).all()
        first[1].created_at = first[0].created_at
        first[1].current_review_status = ReviewStatus.REJECTED
        assert semantic_projection(((first[0], 0), (first[1], 0))).state == "mixed"


def test_corrected_effective_payload_and_capabilities(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        screen = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == seeded["first"])
        )
        proposal = session.scalar(
            select(SemanticProposal).where(SemanticProposal.screen_knowledge_item_id == screen.id)
        )
        corrected = {
            **proposal.source_payload,
            "purpose_summary": "Permite consultar y exportar datos.",
            "supported_capabilities": [
                {"statement": "Consulta.", "evidence_refs": ["control:buscar"]},
                {"statement": "Exporta.", "evidence_refs": ["control:buscar"]},
            ],
        }
        proposal.current_review_status = ReviewStatus.CORRECTED
        session.add(
            SemanticReviewAction(
                semantic_proposal_id=proposal.id,
                action="correct",
                previous_status=ReviewStatus.PENDING_REVIEW,
                new_status=ReviewStatus.CORRECTED,
                corrected_payload=corrected,
                reviewer_subject="reviewer:test",
                proposal_content_hash=HASH,
                source="admin_api",
            )
        )
    tree = client.get("/api/admin/knowledge-tree").json()["erps"][0]
    target = next(
        screen
        for module in tree["modules"]
        for screen in module["screens"]
        if screen["screen_id"] == seeded["first"]
    )
    assert target["semantic_state"] == "corrected"
    assert target["capabilities_count"] == 2
    context = client.get(f"/api/admin/screens/{seeded['first']}/review-context").json()
    assert context["effective_payload"]["purpose_summary"] == corrected["purpose_summary"]


def test_context_without_proposal_module_or_with_corrupt_data_is_safe(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(seeded["version_id"]))
        moduleless = _add_item(
            session,
            version,
            _screen("screen:moduleless", "Moduleless", None),
            "screen",
        )
        corrupt = _add_item(
            session,
            version,
            {"id": "screen:corrupt"},
            "screen",
        )
    moduleless_context = client.get(f"/api/admin/screens/{moduleless.canonical_id}/review-context")
    assert moduleless_context.status_code == 200
    assert moduleless_context.json()["module"] is None
    assert moduleless_context.json()["semantic_state"] == "no_proposal"
    assert moduleless_context.json()["structural_evidence"]["evidence_available"] is True
    corrupt_context = client.get(f"/api/admin/screens/{corrupt.canonical_id}/review-context")
    assert corrupt_context.status_code == 200
    assert corrupt_context.json()["semantic_state"] == "unavailable"
    assert corrupt_context.json()["screen"]["structural_available"] is False
    invalid_context = client.get("/api/admin/screens/screen:u/review-context")
    assert invalid_context.status_code == 200
    body = invalid_context.json()
    assert body["semantic_state"] == "unavailable"
    assert body["active_proposal"]["effective_payload"] is None
    assert body["active_proposal"]["diagnostic"]
    assert "invalid" not in invalid_context.text.casefold()
    unavailable = client.get("/api/admin/screens?semantic_status=unavailable")
    assert unavailable.status_code == 200
    assert unavailable.json()["total"] == 2
    assert {item["screen"]["screen_id"] for item in unavailable.json()["items"]} == {
        "screen:u",
        "screen:corrupt",
    }


def test_invalid_evidence_and_history_payload_do_not_fail_context(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(
                SemanticProposal.screen_knowledge_item.has(
                    KnowledgeItem.canonical_id == seeded["second"]
                )
            )
        )
        session.execute(
            update(SemanticProposal)
            .where(SemanticProposal.id == proposal.id)
            .values(evidence_payload={"invalid": True})
        )
        action = session.scalar(
            select(SemanticReviewAction).where(
                SemanticReviewAction.semantic_proposal_id == proposal.id
            )
        )
        session.execute(
            update(SemanticReviewAction)
            .where(SemanticReviewAction.id == action.id)
            .values(corrected_payload={"invalid": True})
        )
    response = client.get(f"/api/admin/screens/{seeded['second']}/review-context")
    assert response.status_code == 200
    body = response.json()
    assert body["semantic_proposals"][0]["evidence"]["evidence_available"] is False
    assert body["review_history"][0]["corrected_payload"] is None
    assert body["review_history"][0]["semantic_id"]
    assert body["review_history"][0]["diagnostic"]


def test_screen_pagination_is_sql_and_does_not_materialize_large_tree(admin_api):
    client, factory, engine = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(seeded["version_id"]))
        for index in range(250):
            _add_item(
                session,
                version,
                _screen(f"screen:bulk:{index:03}", f"Bulk {index:03}", "module:a"),
                "screen",
            )
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(
            "/api/admin/screens?erp_id=erp:tree&module_id=module:a&text=bulk&limit=3&offset=7"
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 250
    assert len(response.json()["items"]) == 3
    page_sql = [sql.upper() for sql in statements if " LIMIT " in sql.upper()]
    assert page_sql and " OFFSET " in page_sql[0]
    assert any("LOWER" in sql and "KNOWLEDGE_VERSIONS" in sql for sql in page_sql)


def test_latest_active_version_is_unique_per_erp(admin_api):
    client, factory, _ = admin_api
    seed_tree(factory)
    with factory.begin() as session:
        erp = session.get(ERPSystemRecord, "erp:tree")
        run = ImportRun(
            erp=erp,
            source_knowledge_path="new.json",
            source_manifest_path="new-manifest.json",
            requested_knowledge_version="v-new",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.0",
            knowledge_version="v-new",
            canonical_hash=HASH,
            generated_at=datetime.now(timezone.utc),
            imported_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        _add_item(session, version, _screen("screen:new", "Newest", None), "screen")
    tree = client.get("/api/admin/knowledge-tree?erp_id=erp:tree").json()["erps"]
    assert len(tree) == 1
    assert tree[0]["knowledge_version"] == "v-new"
    assert tree[0]["unassigned_screens"][0]["screen_id"] == "screen:new"


def test_semantic_filters_are_exact_for_invalid_payloads(admin_api):
    client, factory, _ = admin_api
    seed_tree(factory)
    pending = client.get("/api/admin/screens?semantic_status=pending_review").json()
    assert pending["total"] == 1
    assert all(item["screen"]["semantic_state"] == "pending_review" for item in pending["items"])
    with factory.begin() as session:
        invalid = session.scalar(
            select(SemanticProposal).where(SemanticProposal.source_payload == {"invalid": True})
        )
        invalid.current_review_status = ReviewStatus.CORRECTED
    corrected = client.get("/api/admin/screens?semantic_status=corrected").json()
    assert all(item["screen"]["semantic_state"] == "corrected" for item in corrected["items"])
    assert "screen:u" not in {item["screen"]["screen_id"] for item in corrected["items"]}
    unavailable = client.get("/api/admin/screens?semantic_status=unavailable").json()
    assert "screen:u" in {item["screen"]["screen_id"] for item in unavailable["items"]}


def test_effective_title_search_order_approve_and_reset(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    moment = datetime.now(timezone.utc)
    with factory.begin() as session:
        screen = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == seeded["first"])
        )
        corrected = {
            **screen.source_payload,
            "title": "Corrected Searchable Title",
            "normalized_title": "corrected searchable title",
            "route": "/corrected-searchable",
        }
        session.add_all(
            [
                ReviewAction(
                    knowledge_item_id=screen.id,
                    action="correct",
                    previous_status=ReviewStatus.APPROVED,
                    new_status=ReviewStatus.CORRECTED,
                    corrected_payload=corrected,
                    item_content_hash=HASH,
                    source="api",
                    created_at=moment,
                ),
                ReviewAction(
                    knowledge_item_id=screen.id,
                    action="approve",
                    previous_status=ReviewStatus.CORRECTED,
                    new_status=ReviewStatus.APPROVED,
                    item_content_hash=HASH,
                    source="api",
                    created_at=moment + timedelta(seconds=1),
                ),
            ]
        )
    found = client.get("/api/admin/screens?text=corrected").json()
    assert found["total"] == 2  # includes the pre-existing corrected structural screen
    target = next(item for item in found["items"] if item["screen"]["screen_id"] == seeded["first"])
    assert target["screen"]["title"] == "Corrected Searchable Title"
    assert client.get("/api/admin/screens?text=zulu").json()["total"] == 0
    all_rows = client.get("/api/admin/screens").json()["items"]
    titles = [item["screen"]["title"] for item in all_rows]
    assert titles == sorted(titles, key=str.casefold)
    with factory.begin() as session:
        screen = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == seeded["first"])
        )
        session.add_all(
            [
                ReviewAction(
                    knowledge_item_id=screen.id,
                    action="reset_to_pending",
                    previous_status=ReviewStatus.APPROVED,
                    new_status=ReviewStatus.PENDING_REVIEW,
                    item_content_hash=HASH,
                    source="api",
                    created_at=moment + timedelta(seconds=2),
                ),
                ReviewAction(
                    knowledge_item_id=screen.id,
                    action="approve",
                    previous_status=ReviewStatus.PENDING_REVIEW,
                    new_status=ReviewStatus.APPROVED,
                    item_content_hash=HASH,
                    source="api",
                    created_at=moment + timedelta(seconds=3),
                ),
            ]
        )
    assert client.get("/api/admin/screens?text=corrected-searchable").json()["total"] == 0
    assert client.get("/api/admin/screens?text=zulu").json()["total"] == 1


def test_semantic_filter_scans_large_candidate_set_in_bounded_batches(admin_api):
    client, factory, engine = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(seeded["version_id"]))
        session.add_all(
            [
                KnowledgeItem(
                    knowledge_version=version,
                    canonical_id=f"screen:large:{index:04}",
                    entity_type="screen",
                    title=f"Large {index:04}",
                    normalized_title=f"large {index:04}",
                    route=f"/large/{index:04}",
                    content_hash=HASH,
                    source_payload=_screen(f"screen:large:{index:04}", f"Large {index:04}", None),
                    generated_review_status=ReviewStatus.APPROVED,
                    current_review_status=ReviewStatus.APPROVED,
                )
                for index in range(2000)
            ]
        )
    statements = []

    def capture(_conn, _cursor, statement, parameters, *_args):
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get("/api/admin/screens?semantic_status=no_proposal&limit=5&offset=1990")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.json()["total"] == 2000
    assert len(response.json()["items"]) == 5
    batch_queries = [sql for sql, _params in statements if " LIMIT " in sql.upper()]
    assert len(batch_queries) >= 10
    assert all("LIMIT" in sql.upper() for sql in batch_queries)


def test_complete_evidence_ids_and_invalid_secondary_items_are_tolerated(admin_api):
    client, factory, _ = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(seeded["version_id"]))
        screen = session.scalar(
            select(KnowledgeItem).where(KnowledgeItem.canonical_id == seeded["second"])
        )
        screen_payload = {
            **screen.source_payload,
            "evidence_ids": ["evidence:screen", "evidence:bad"],
        }
        session.execute(
            update(KnowledgeItem)
            .where(KnowledgeItem.id == screen.id)
            .values(source_payload=screen_payload)
        )
        valid_field = {
            "id": "field:valid",
            "screen_id": screen.canonical_id,
            "label": "Valid",
            "normalized_label": "valid",
            "required": False,
            "readonly": False,
            "disabled": False,
            "evidence_ids": ["evidence:field"],
            "review_status": "approved",
        }
        table = {
            "id": "table:one",
            "screen_id": screen.canonical_id,
            "name": "One",
            "column_ids": ["column:one"],
            "evidence_ids": [],
            "review_status": "approved",
        }
        column = {
            "id": "column:one",
            "table_id": "table:one",
            "name": "Column",
            "normalized_name": "column",
            "position": 0,
            "evidence_ids": ["evidence:column"],
            "review_status": "approved",
        }
        _add_item(session, version, valid_field, "field")
        _add_item(session, version, table, "table")
        _add_item(session, version, column, "table_column")
        invalid_payloads = [
            ("field", {**valid_field, "id": "field:bad", "input_type": []}),
            (
                "control",
                {
                    "id": "control:bad",
                    "screen_id": screen.canonical_id,
                    "label": "Bad",
                    "control_type": [],
                    "mutative": False,
                    "review_status": "approved",
                },
            ),
            (
                "ui_state",
                {
                    "id": "state:bad",
                    "screen_id": screen.canonical_id,
                    "title": "Bad",
                    "depth": {},
                    "review_status": "approved",
                },
            ),
            (
                "table_column",
                {**column, "id": "column:bad", "name": []},
            ),
        ]
        for entity_type, payload in invalid_payloads:
            _add_item(session, version, payload, entity_type)
        _add_item(
            session,
            version,
            {
                "id": "transition:bad",
                "source_state_id": "state:bad",
                "target_state_id": "state:bad",
                "trigger_control_id": "control:missing",
                "category": "click",
                "review_status": "approved",
            },
            "transition",
        )
        _add_item(
            session,
            version,
            {"id": "evidence:bad"},
            "evidence",
        )
    response = client.get(f"/api/admin/screens/{seeded['second']}/review-context")
    assert response.status_code == 200, response.text
    evidence = response.json()["structural_evidence"]
    assert evidence["evidence_available"] is True
    assert evidence["fields"][0]["field_id"] == "field:valid"
    assert {
        "evidence:screen",
        "evidence:module",
        "evidence:field",
        "evidence:column",
    } <= set(evidence["evidence_ids"])
    warnings = " ".join(evidence["warnings"])
    for identifier in (
        "field:bad",
        "control:bad",
        "state:bad",
        "column:bad",
        "evidence:bad",
    ):
        assert identifier in warnings


def test_review_context_does_not_load_unrelated_version_items(admin_api):
    client, factory, engine = admin_api
    seeded = seed_tree(factory)
    with factory.begin() as session:
        version = session.get(KnowledgeVersionRecord, uuid.UUID(seeded["version_id"]))
        session.add_all(
            [
                KnowledgeItem(
                    knowledge_version=version,
                    canonical_id=f"field:unrelated:{index}",
                    entity_type="field",
                    title="Unrelated",
                    content_hash=HASH,
                    source_payload={
                        "id": f"field:unrelated:{index}",
                        "screen_id": "screen:other",
                        "label": "Unrelated",
                        "normalized_label": "unrelated",
                        "review_status": "approved",
                    },
                    generated_review_status=ReviewStatus.APPROVED,
                    current_review_status=ReviewStatus.APPROVED,
                )
                for index in range(500)
            ]
        )
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.get(f"/api/admin/screens/{seeded['second']}/review-context")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert "unrelated" not in response.text.casefold()
    assert len(statements) <= 15
    assert not any("ORDER BY knowledge_items.entity_type" in statement for statement in statements)
