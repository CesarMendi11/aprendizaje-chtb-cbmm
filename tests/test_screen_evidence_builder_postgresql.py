from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.analysis.evidence import ScreenEvidenceBuilder
from src.database.enums import ReviewActionType, ReviewSource
from src.database.models import ReviewAction
from src.knowledge.canonical.enums import ReviewStatus
from tests.test_screen_evidence_builder import add_correction, add_item, seed_screen, seed_version


def _test_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL no configurada")
    database = urlsplit(url).path.lstrip("/").casefold()
    if "semantic_test" not in database:
        pytest.fail("TEST_DATABASE_URL no apunta a una base semantic_test")
    return url


@pytest.fixture(scope="module")
def pg_engine():
    engine = sa.create_engine(_test_url(), pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail("Las pruebas requieren PostgreSQL")
    yield engine
    engine.dispose()


@pytest.mark.postgresql
def test_postgresql_builder_is_reproducible_read_only_and_isolated(pg_engine):
    with Session(pg_engine, expire_on_commit=False) as setup, setup.begin():
        version, module, screen = seed_screen(setup)
        other_screen = add_item(
            setup,
            version,
            "screen",
            "screen:postgres-other",
            {
                "id": "screen:postgres-other",
                "erp_id": version.erp_id,
                "module_id": module.canonical_id,
                "title": "PostgreSQL other",
                "route": "/postgres-other",
            },
        )
        field = add_item(
            setup,
            version,
            "field",
            "field:postgres-corrected",
            {
                "id": "field:postgres-corrected",
                "screen_id": screen.canonical_id,
                "label": "Original",
            },
            ReviewStatus.CORRECTED,
        )
        setup.add(
            ReviewAction(
                knowledge_item_id=field.id,
                action=ReviewActionType.CORRECT,
                previous_status=ReviewStatus.PENDING_REVIEW,
                new_status=ReviewStatus.CORRECTED,
                corrected_payload={
                    "id": field.canonical_id,
                    "screen_id": screen.canonical_id,
                    "label": "Corregido",
                },
                review_notes="synthetic",
                reviewer_subject="test",
                item_content_hash=field.content_hash,
                source=ReviewSource.CLI,
            )
        )
        moved = add_item(
            setup,
            version,
            "field",
            "field:postgres-moved",
            {
                "id": "field:postgres-moved",
                "screen_id": screen.canonical_id,
                "label": "Movido",
            },
        )
        add_correction(
            setup,
            moved,
            {**moved.source_payload, "screen_id": other_screen.canonical_id},
        )
        add_item(
            setup,
            version,
            "field",
            "field:postgres-pending",
            {
                "id": "field:postgres-pending",
                "screen_id": screen.canonical_id,
                "label": "Pendiente",
            },
            ReviewStatus.PENDING_REVIEW,
        )
        other_version = seed_version(setup)
        add_item(
            setup,
            other_version,
            "field",
            "field:other-version",
            {
                "id": "field:other-version",
                "screen_id": screen.canonical_id,
                "label": "Otra versión",
            },
        )
        other_version_id = other_version.id
        version_id, screen_id, other_screen_id = version.id, screen.id, other_screen.id

    with Session(pg_engine) as first_session:
        before = (set(first_session.new), set(first_session.dirty), set(first_session.deleted))
        first = ScreenEvidenceBuilder(first_session).build(version_id, screen_id)
        assert (
            set(first_session.new),
            set(first_session.dirty),
            set(first_session.deleted),
        ) == before
        assert [field.label for field in first.fields] == ["Corregido"]
        assert "Otra versión" not in first.model_dump_json()
        assert "Pendiente" not in first.model_dump_json()
        first_hash = first.evidence_hash

    with Session(pg_engine) as second_session:
        second = ScreenEvidenceBuilder(second_session).build(version_id, screen_id)
        assert second.evidence_hash == first_hash
        assert second.knowledge_version_id != other_version_id
        moved_package = ScreenEvidenceBuilder(second_session).build(version_id, other_screen_id)
        assert [field.field_id for field in moved_package.fields] == ["field:postgres-moved"]
