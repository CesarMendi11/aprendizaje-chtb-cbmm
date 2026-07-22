from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from test_admin_knowledge_api import HASH, seed_tree
from test_semantic_review_api import Client

from src.api.app import create_app
from src.api.dependencies import get_admin_read_session
from src.config.api_settings import ApiSettings
from src.database.enums import ImportStatus, KnowledgeVersionStatus
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeVersionRecord,
    ReviewAction,
    SemanticProposal,
    SemanticReviewAction,
)


def _test_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    if "semantic_test" not in urlsplit(url).path.lstrip("/").casefold():
        pytest.fail("TEST_DATABASE_URL debe apuntar a una base temporal semantic_test")
    return url


@pytest.mark.postgresql
def test_postgresql_admin_tree_isolated_paginated_visible_and_read_only(tmp_path):
    engine = sa.create_engine(_test_url(), pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seeded = seed_tree(factory)
    with factory.begin() as session:
        erp = ERPSystemRecord(
            id="erp:other", slug="other", name="Other ERP", profile_name="test", safe_metadata={}
        )
        run = ImportRun(
            erp=erp,
            source_knowledge_path="synthetic.json",
            source_manifest_path="manifest.json",
            requested_knowledge_version="v2",
            status=ImportStatus.SUCCEEDED,
            source_hashes={},
        )
        session.add(
            KnowledgeVersionRecord(
                erp=erp,
                import_run=run,
                schema_version="1.0",
                knowledge_version="v2",
                canonical_hash=HASH,
                generated_at=datetime.now(timezone.utc),
                entity_counts={},
                source_artifact_hashes={},
                build_warnings=[],
                status=KnowledgeVersionStatus.ACTIVE,
            )
        )
    index = tmp_path / "screen_index.json"
    index.write_text('{"screens": []}', encoding="utf-8")
    app = create_app(
        replace(ApiSettings(), screen_index_path=index, semantic_review_api_enabled=True),
        semantic_review_session_factory=factory,
    )
    client = Client(app)
    with Session(engine) as verification:
        before = {
            model: verification.scalar(select(func.count()).select_from(model))
            for model in (ReviewAction, SemanticProposal, SemanticReviewAction)
        }
    tree = client.get("/api/admin/knowledge-tree")
    assert tree.status_code == 200, tree.text
    assert {erp["erp_id"] for erp in tree.json()["erps"]} == {"erp:tree", "erp:other"}
    isolated = client.get("/api/admin/knowledge-tree?erp_id=erp:tree")
    assert len(isolated.json()["erps"]) == 1
    page = client.get("/api/admin/screens?erp=erp:tree&limit=1&offset=1")
    assert page.json()["total"] == 3
    context = client.get(f"/api/admin/screens/{seeded['second']}/review-context")
    assert context.json()["traceability"]["review_action_count"] == 1
    request = type("Request", (), {"app": type("App", (), {"state": type("State", (), {})()})()})()
    request.app.state.semantic_review_session_factory = factory
    dependency = get_admin_read_session(request)
    admin_session = next(dependency)
    assert admin_session.execute(sa.text("SHOW transaction_read_only")).scalar() == "on"
    with pytest.raises(sa.exc.InternalError):
        admin_session.execute(
            sa.text(
                "INSERT INTO erp_systems "
                "(id, slug, name, profile_name, safe_metadata, created_at, updated_at) "
                "VALUES ('forbidden', 'forbidden', 'Forbidden', 'test', '{}', now(), now())"
            )
        )
    with pytest.raises(StopIteration):
        next(dependency)
    with Session(engine) as second_session:
        assert (
            second_session.scalar(
                select(func.count())
                .select_from(ERPSystemRecord)
                .where(ERPSystemRecord.id == "forbidden")
            )
            == 0
        )
        for model, count in before.items():
            assert second_session.scalar(select(func.count()).select_from(model)) == count
    engine.dispose()
