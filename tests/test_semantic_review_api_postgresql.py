from __future__ import annotations

import os
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker
from test_semantic_review_api import Client, action_body, seed

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.database.models import SemanticProposal, SemanticReviewAction
from src.knowledge.canonical.enums import ReviewStatus


def _test_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    if "semantic_test" not in urlsplit(url).path.lstrip("/").casefold():
        pytest.fail("TEST_DATABASE_URL debe apuntar a una base temporal semantic_test")
    return url


@pytest.fixture(scope="module")
def pg_engine():
    engine = sa.create_engine(_test_url(), pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        pytest.fail("La API semántica requiere PostgreSQL real")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_api(pg_engine, tmp_path):
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    factory = sessionmaker(bind=pg_engine, expire_on_commit=False)
    settings = replace(
        ApiSettings(),
        screen_index_path=index,
        semantic_review_api_enabled=True,
        semantic_review_allow_remote=False,
    )
    return Client(create_app(settings, semantic_review_session_factory=factory)), factory


@pytest.mark.postgresql
def test_postgresql_http_review_workflow_pagination_isolation_and_atomicity(
    pg_api, pg_engine
):
    client, factory = pg_api
    pending_id, pending_source, pending_evidence = seed(factory, suffix="pg-pending")
    approved_id, approved_source, approved_evidence = seed(factory, suffix="pg-approved")
    corrected_id, corrected_source, corrected_evidence = seed(factory, suffix="pg-corrected")
    rejected_id, rejected_source, rejected_evidence = seed(factory, suffix="pg-rejected")
    invalid_id, invalid_source, _ = seed(factory, suffix="pg-invalid")

    statements = []

    def record_statement(*args):
        statements.append(str(args[2]))

    event.listen(pg_engine, "before_cursor_execute", record_statement)
    try:
        page = client.get(
            "/api/admin/semantic-proposals?status=pending_review&erp_id=erp:pg-pending"
            "&limit=1&offset=0"
        )
    finally:
        event.remove(pg_engine, "before_cursor_execute", record_statement)
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["semantic_id"] == pending_id
    assert len(statements) <= 3

    detail = client.get(f"/api/admin/semantic-proposals/{pending_id}").json()
    assert detail["evidence"]["evidence_available"] is True
    assert detail["review_history"] == []
    serialized = str(detail).casefold()
    assert all(value not in serialized for value in ("password", "cookie", "raw_response"))
    pending_effective = client.get(
        f"/api/admin/semantic-proposals/{pending_id}/effective"
    ).json()
    assert pending_effective["effective_payload"] == pending_source
    assert pending_effective["publishable_payload"] is None

    assert client.post(
        f"/api/admin/semantic-proposals/{approved_id}/approve", json=action_body()
    ).status_code == 200
    corrected = {
        **corrected_source,
        "purpose_summary": "Permite buscar datos por Código.",
        "supported_capabilities": [
            {
                "statement": "Permite buscar registros sintéticos.",
                "evidence_refs": ["control:pg-corrected"],
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

    assert client.post(
        f"/api/admin/semantic-proposals/{pending_id}/approve",
        json=action_body(expected_status="approved"),
    ).status_code == 409
    assert client.post(
        f"/api/admin/semantic-proposals/{pending_id}/approve",
        json=action_body(expected_revision=1),
    ).status_code == 409
    invalid = {
        **invalid_source,
        "purpose_summary": "Permite buscar datos por Código.",
        "supported_capabilities": [
            {"statement": "Permite buscar registros.", "evidence_refs": ["control:unknown"]}
        ],
    }
    assert client.post(
        f"/api/admin/semantic-proposals/{invalid_id}/correct",
        json=action_body(corrected_payload=invalid),
    ).status_code == 422

    with Session(pg_engine) as verification:
        proposals = {
            proposal.semantic_id: proposal
            for proposal in verification.scalars(
                select(SemanticProposal).where(
                    SemanticProposal.semantic_id.in_(
                        [pending_id, approved_id, corrected_id, rejected_id, invalid_id]
                    )
                )
            )
        }
        assert proposals[approved_id].source_payload == approved_source
        assert proposals[approved_id].evidence_payload == approved_evidence
        assert proposals[corrected_id].source_payload == corrected_source
        assert proposals[corrected_id].evidence_payload == corrected_evidence
        assert proposals[rejected_id].source_payload == rejected_source
        assert proposals[rejected_id].evidence_payload == rejected_evidence
        assert proposals[approved_id].current_review_status == ReviewStatus.APPROVED
        assert proposals[corrected_id].current_review_status == ReviewStatus.CORRECTED
        assert proposals[rejected_id].current_review_status == ReviewStatus.REJECTED
        action_counts = dict(
            verification.execute(
                select(SemanticProposal.semantic_id, func.count(SemanticReviewAction.id))
                .outerjoin(SemanticReviewAction)
                .where(
                    SemanticProposal.semantic_id.in_(
                        [pending_id, approved_id, corrected_id, rejected_id, invalid_id]
                    )
                )
                .group_by(SemanticProposal.semantic_id)
            ).all()
        )
        assert action_counts[approved_id] == 1
        assert action_counts[corrected_id] == 1
        assert action_counts[rejected_id] == 1
        assert action_counts[pending_id] == 0
        assert action_counts[invalid_id] == 0

    approved_effective = client.get(
        f"/api/admin/semantic-proposals/{approved_id}/effective"
    ).json()
    corrected_effective = client.get(
        f"/api/admin/semantic-proposals/{corrected_id}/effective"
    ).json()
    rejected_effective = client.get(
        f"/api/admin/semantic-proposals/{rejected_id}/effective"
    ).json()
    assert approved_effective["publishable_payload"] == approved_source
    assert corrected_effective["publishable_payload"] == corrected
    assert rejected_effective["publishable_payload"] is None

    assert client.get(
        "/api/admin/semantic-proposals?erp_id=erp:pg-pending"
    ).json()["total"] == 1
    version_id = page.json()["items"][0]["knowledge_version_id"]
    assert client.get(
        f"/api/admin/semantic-proposals?knowledge_version_id={version_id}"
    ).json()["total"] == 1
