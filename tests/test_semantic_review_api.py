from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from src.analysis.schemas import ScreenEvidencePackage
from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.base import Base
from src.database.enums import ImportStatus, KnowledgeVersionStatus, SemanticType
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from src.database.services.semantic_payloads import (
    canonical_json_hash,
    validated_semantic_evidence_snapshot,
)
from src.database.services.semantic_proposal_service import SemanticProposalService
from src.knowledge.canonical.enums import ReviewStatus

HASH = "a" * 64


class Client:
    def __init__(self, app, *, client=("127.0.0.1", 50000)):
        self.app = app
        self.client = client

    def request(self, method, path, **kwargs):
        async def send():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app, client=self.client),
                base_url="http://test",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)


@pytest.fixture
def api(tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    settings = replace(
        ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True
    )
    database_path = tmp_path / "semantic_review.sqlite3"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(settings, semantic_review_session_factory=factory)
    yield Client(app), factory
    engine.dispose()
    database_path.unlink(missing_ok=True)
    assert not database_path.exists()


def seed(factory, *, suffix="one"):
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id=f"erp:{suffix}",
            slug=f"erp-{suffix}",
            name="Synthetic ERP",
            profile_name="test",
            safe_metadata={},
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="synthetic.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version=suffix,
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        version = KnowledgeVersionRecord(
            erp=erp,
            import_run=run,
            schema_version="1.0",
            knowledge_version=suffix,
            canonical_hash=HASH,
            generated_at=datetime.now(timezone.utc),
            entity_counts={},
            source_artifact_hashes={},
            build_warnings=[],
            status=KnowledgeVersionStatus.ACTIVE,
        )
        screen = KnowledgeItem(
            knowledge_version=version,
            canonical_id=f"screen:{suffix}",
            entity_type="screen",
            title="Synthetic Screen",
            normalized_title="synthetic screen",
            route=f"/synthetic/{suffix}",
            content_hash=HASH,
            source_payload={"id": f"screen:{suffix}"},
            generated_review_status=ReviewStatus.APPROVED,
            current_review_status=ReviewStatus.APPROVED,
        )
        session.add(screen)
        session.flush()
        raw = {
            "schema_version": "1.0",
            "erp_id": erp.id,
            "knowledge_version_id": str(version.id),
            "knowledge_version": suffix,
            "screen_id": screen.canonical_id,
            "screen_title": screen.title,
            "screen_route": screen.route,
            "module": {"module_id": f"module:{suffix}", "name": "Synthetic"},
            "fields": [
                {
                    "field_id": f"field:{suffix}",
                    "label": "Código",
                    "input_type": "text",
                    "required": False,
                    "readonly": False,
                }
            ],
            "controls": [
                {
                    "control_id": f"control:{suffix}",
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
            "main_content_text": "Synthetic safe text",
            "evidence_ids": [],
            "warnings": [],
        }
        digest = canonical_json_hash(raw)
        package = ScreenEvidencePackage.model_validate({**raw, "evidence_hash": digest})
        source = {
            "semantic_type": "screen_purpose",
            "screen_id": screen.canonical_id,
            "purpose_summary": "Permite buscar registros sintéticos.",
            "supported_capabilities": [
                {
                    "statement": "Permite buscar registros sintéticos.",
                    "evidence_refs": [f"control:{suffix}"],
                }
            ],
            "limitations": [],
            "uncertainties": [],
        }
        proposal = SemanticProposalService(session).create_pending_proposal(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            source_payload=source,
            evidence_payload=validated_semantic_evidence_snapshot(package),
            evidence_ids=list(package.evidence_ids),
            generation_model="synthetic-model",
            prompt_version="synthetic-v1",
            prompt_hash="b" * 64,
            generation_parameters={"temperature": 0},
        )
        semantic_id = proposal.semantic_id
    return semantic_id, source, raw


def action_body(**changes):
    body = {
        "reviewer_id": "reviewer:local",
        "reason": "Revisión manual sintética.",
        "expected_status": "pending_review",
        "expected_revision": 0,
    }
    body.update(changes)
    return body


def test_disabled_by_default_and_absent_from_openapi(tmp_path):
    index = tmp_path / "index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    app = create_app(
        replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=False)
    )
    client = Client(app)
    assert client.get("/api/admin/semantic-proposals").status_code == 404
    assert "/api/admin/semantic-proposals" not in client.get("/openapi.json").json()["paths"]


def test_empty_list_and_openapi_notice(api):
    client, _ = api
    response = client.get("/api/admin/semantic-proposals")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert "/api/admin/semantic-proposals" in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_clients_are_allowed(api, host):
    client, _ = api
    assert Client(client.app, client=(host, 50000)).get(
        "/api/admin/semantic-proposals"
    ).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Forwarded-For": "127.0.0.1"},
        {"Forwarded": "for=127.0.0.1"},
    ],
)
def test_remote_client_is_hidden_before_session_creation(api, headers):
    client, _ = api
    opened = 0
    original_factory = client.app.state.semantic_review_session_factory

    def counted_factory():
        nonlocal opened
        opened += 1
        return original_factory()

    client.app.state.semantic_review_session_factory = counted_factory
    response = Client(client.app, client=("198.51.100.20", 50000)).get(
        "/api/admin/semantic-proposals", headers=headers
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert opened == 0


def test_allow_remote_permits_synthetic_remote_client(api):
    client, factory = api
    settings = replace(client.app.state.settings, semantic_review_allow_remote=True)
    remote_app = create_app(settings, semantic_review_session_factory=factory)
    response = Client(remote_app, client=("198.51.100.20", 50000)).get(
        "/api/admin/semantic-proposals"
    )
    assert response.status_code == 200


def test_concurrent_requests_use_independent_sessions_and_http_transactions(api):
    client, factory = api
    semantic_id, source, _ = seed(factory, suffix="transactions")
    opened: list[int] = []
    commits: list[int] = []
    rollbacks: list[int] = []

    def tracking_factory():
        session = factory()
        identifier = id(session)
        opened.append(identifier)
        original_commit = session.commit
        original_rollback = session.rollback

        def commit():
            commits.append(identifier)
            return original_commit()

        def rollback():
            rollbacks.append(identifier)
            return original_rollback()

        session.commit = commit
        session.rollback = rollback
        return session

    client.app.state.semantic_review_session_factory = tracking_factory

    async def concurrent_gets():
        transport = httpx.ASGITransport(app=client.app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            return await asyncio.gather(
                http.get("/api/admin/semantic-proposals"),
                http.get(f"/api/admin/semantic-proposals/{semantic_id}"),
            )

    responses = asyncio.run(concurrent_gets())
    assert [response.status_code for response in responses] == [200, 200]
    assert len(set(opened)) == 2
    assert sorted(commits) == sorted(opened)
    invalid = {
        **source,
        "purpose_summary": "Permite buscar datos por Código.",
        "supported_capabilities": [
            {"statement": "Permite buscar registros.", "evidence_refs": ["control:unknown"]}
        ],
    }
    response = Client(client.app).post(
        f"/api/admin/semantic-proposals/{semantic_id}/correct",
        json=action_body(corrected_payload=invalid),
    )
    assert response.status_code == 422
    assert len(rollbacks) == 1


def test_pagination_filters_detail_and_sanitized_evidence(api):
    client, factory = api
    first, source, _ = seed(factory, suffix="first")
    seed(factory, suffix="second")
    page = client.get(
        "/api/admin/semantic-proposals?status=pending_review&semantic_type=screen_purpose"
        "&erp_id=erp:first&limit=1&offset=0"
    ).json()
    assert page["total"] == 1 and len(page["items"]) == 1
    detail = client.get(f"/api/admin/semantic-proposals/{first}").json()
    assert detail["source_payload"] == source
    assert detail["review_history"] == []
    assert detail["evidence"]["evidence_available"] is True
    serialized = str(detail).casefold()
    assert all(word not in serialized for word in ("password", "cookie", "raw_response", "html"))
    assert client.get("/api/admin/semantic-proposals/missing").status_code == 404


def test_effective_contract_for_every_review_status(api):
    client, factory = api
    pending_id, pending_source, _ = seed(factory, suffix="effective-pending")
    approved_id, approved_source, _ = seed(factory, suffix="effective-approved")
    corrected_id, corrected_source, _ = seed(factory, suffix="effective-corrected")
    rejected_id, rejected_source, _ = seed(factory, suffix="effective-rejected")
    assert client.post(
        f"/api/admin/semantic-proposals/{approved_id}/approve", json=action_body()
    ).status_code == 200
    corrected = {
        **corrected_source,
        "purpose_summary": "Permite buscar datos por Código.",
        "supported_capabilities": [
            {
                "statement": "Permite buscar registros sintéticos.",
                "evidence_refs": ["control:effective-corrected"],
            }
        ],
    }
    assert client.post(
        f"/api/admin/semantic-proposals/{corrected_id}/correct",
        json=action_body(corrected_payload=corrected),
    ).status_code == 200
    assert client.post(
        f"/api/admin/semantic-proposals/{rejected_id}/reject", json=action_body()
    ).status_code == 200
    cases = (
        (pending_id, pending_source, None),
        (approved_id, approved_source, approved_source),
        (corrected_id, corrected, corrected),
        (rejected_id, rejected_source, None),
    )
    for semantic_id, effective, publishable in cases:
        payload = client.get(
            f"/api/admin/semantic-proposals/{semantic_id}/effective"
        ).json()
        assert payload["effective_payload"] == effective
        assert payload["publishable_payload"] == publishable


def test_admin_handlers_do_not_change_non_admin_errors(api):
    client, _ = api
    chat = client.post("/api/chat", json={"question": ""})
    assert chat.status_code == 422
    assert "detail" in chat.json()
    assert client.get("/api/health").status_code == 200
    assert client.get("/route-that-does-not-exist").json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    ("action", "status", "publishable"),
    [("approve", "approved", True), ("reject", "rejected", False)],
)
def test_approve_and_reject_use_append_only_actions(api, action, status, publishable):
    client, factory = api
    semantic_id, original, evidence = seed(factory, suffix=action)
    response = client.post(
        f"/api/admin/semantic-proposals/{semantic_id}/{action}", json=action_body()
    )
    assert response.status_code == 200
    assert response.json()["current_review_status"] == status
    assert (response.json()["publishable_payload"] is not None) is publishable
    with factory() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(SemanticProposal.semantic_id == semantic_id)
        )
        assert proposal.source_payload == original
        assert proposal.evidence_payload == evidence
        assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == 1
    assert client.post(
        f"/api/admin/semantic-proposals/{semantic_id}/{action}", json=action_body()
    ).status_code == 409


def test_valid_correction_becomes_effective_without_mutating_original(api):
    client, factory = api
    semantic_id, original, evidence = seed(factory, suffix="correct")
    corrected = {
        **original,
        "purpose_summary": "Permite buscar registros por Código.",
        "supported_capabilities": [
            {
                "statement": "Permite buscar registros sintéticos.",
                "evidence_refs": ["control:correct"],
            }
        ],
    }
    response = client.post(
        f"/api/admin/semantic-proposals/{semantic_id}/correct",
        json=action_body(corrected_payload=corrected),
    )
    assert response.status_code == 200, response.text
    assert response.json()["effective_payload"] == corrected
    assert client.get(f"/api/admin/semantic-proposals/{semantic_id}/effective").json()[
        "publishable_payload"
    ] == corrected
    with factory() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(SemanticProposal.semantic_id == semantic_id)
        )
        assert proposal.source_payload == original and proposal.evidence_payload == evidence


@pytest.mark.parametrize(
    "body",
    [
        {"reason": "x", "expected_status": "pending_review", "expected_revision": 0},
        {"reviewer_id": "x", "expected_status": "pending_review", "expected_revision": 0},
        action_body(reviewer_id="<b>reviewer</b>"),
        action_body(reason="javascript:alert(1)"),
    ],
)
def test_reviewer_and_reason_are_strict(api, body):
    client, factory = api
    semantic_id, _, _ = seed(factory, suffix="reviewer-safe")
    assert client.post(
        f"/api/admin/semantic-proposals/{semantic_id}/approve", json=body
    ).status_code == 422


@pytest.mark.parametrize("mutation", ["screen", "type", "ref", "narrative", "action"])
def test_invalid_corrections_are_rejected_and_rolled_back(api, mutation):
    client, factory = api
    semantic_id, corrected, _ = seed(factory, suffix=mutation)
    corrected = {**corrected, "purpose_summary": "Descripción funcional diferente y segura."}
    if mutation == "screen":
        corrected["screen_id"] = "screen:other"
    elif mutation == "type":
        corrected["semantic_type"] = "other"
    elif mutation == "ref":
        corrected["supported_capabilities"][0]["evidence_refs"] = ["control:unknown"]
    elif mutation == "narrative":
        corrected["supported_capabilities"][0]["statement"] = "Usa control:narrative aquí."
    elif mutation == "action":
        corrected["supported_capabilities"][0]["statement"] = "Permite eliminar registros."
    response = client.post(
        f"/api/admin/semantic-proposals/{semantic_id}/correct",
        json=action_body(corrected_payload=corrected),
    )
    assert response.status_code == 422
    with factory() as session:
        proposal = session.scalar(
            select(SemanticProposal).where(SemanticProposal.semantic_id == semantic_id)
        )
        assert proposal.current_review_status == ReviewStatus.PENDING_REVIEW
        assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == 0


@pytest.mark.parametrize(
    "change", [{"expected_status": "approved"}, {"expected_revision": 1}]
)
def test_stale_preconditions_return_409_without_action(api, change):
    client, factory = api
    semantic_id, _, _ = seed(factory, suffix="stale-safe")
    response = client.post(
        f"/api/admin/semantic-proposals/{semantic_id}/approve",
        json=action_body(**change),
    )
    assert response.status_code == 409
    assert response.json()["current_status"] == "pending_review"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SemanticReviewAction)) == 0
