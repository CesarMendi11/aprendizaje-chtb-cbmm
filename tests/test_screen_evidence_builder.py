from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import src.database.models  # noqa: F401
from src.analysis.evidence.screen_evidence_builder import (
    EffectiveContentIntegrityError,
    EvidenceEntityTypeError,
    EvidenceScreenReviewError,
    EvidenceVersionMismatchError,
    ScreenEvidenceBuilder,
    UnsafeScreenRouteError,
)
from src.analysis.schemas import ScreenEvidencePackage
from src.database.base import Base
from src.database.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    ReviewActionType,
    ReviewSource,
)
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    ReviewAction,
)
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.ids import content_hash

HASH = "a" * 64


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value


def seed_version(session, suffix=None):
    suffix = suffix or uuid.uuid4().hex[:16]
    erp = ERPSystemRecord(
        id=f"erp:test:{suffix}",
        slug=f"test-{suffix}",
        name="Synthetic ERP",
        profile_name="evidence-test",
        safe_metadata={},
    )
    run = ImportRun(
        erp=erp,
        source_knowledge_path="synthetic/knowledge.json",
        source_manifest_path="synthetic/manifest.json",
        requested_knowledge_version=suffix,
        status=ImportStatus.SUCCEEDED,
        source_hashes={},
    )
    version = KnowledgeVersionRecord(
        erp=erp,
        import_run=run,
        schema_version="1.0.0",
        knowledge_version=suffix,
        canonical_hash=HASH,
        generated_at=datetime.now(timezone.utc),
        entity_counts={},
        source_artifact_hashes={},
        build_warnings=[],
        status=KnowledgeVersionStatus.IMPORTED,
    )
    session.add(version)
    session.flush()
    return version


def add_item(session, version, entity_type, canonical_id, payload, status=ReviewStatus.APPROVED):
    item = KnowledgeItem(
        knowledge_version_id=version.id,
        canonical_id=canonical_id,
        entity_type=entity_type,
        parent_canonical_id=payload.get("module_id")
        or payload.get("screen_id")
        or payload.get("table_id"),
        title=payload.get("title") or payload.get("label") or payload.get("name"),
        normalized_title=None,
        route=payload.get("route"),
        content_hash=content_hash(payload),
        source_payload=payload,
        generated_review_status=status,
        current_review_status=status,
    )
    session.add(item)
    session.flush()
    return item


def add_correction(session, item, payload):
    item.current_review_status = ReviewStatus.CORRECTED
    session.add(
        ReviewAction(
            knowledge_item_id=item.id,
            action=ReviewActionType.CORRECT,
            previous_status=ReviewStatus.APPROVED,
            new_status=ReviewStatus.CORRECTED,
            corrected_payload=payload,
            review_notes="synthetic correction",
            reviewer_subject="test",
            item_content_hash=item.content_hash,
            source=ReviewSource.CLI,
        )
    )
    session.flush()


def seed_screen(session, *, route="/retenciones?tab=one#top", status=ReviewStatus.APPROVED):
    version = seed_version(session)
    module = add_item(
        session,
        version,
        "module",
        "module:collections",
        {"id": "module:collections", "erp_id": version.erp_id, "name": "Cuentas por cobrar"},
    )
    screen = add_item(
        session,
        version,
        "screen",
        "screen:retenciones",
        {
            "id": "screen:retenciones",
            "erp_id": version.erp_id,
            "module_id": module.canonical_id,
            "title": "Retenciones",
            "route": route,
            "evidence_ids": [" evidence:b ", "evidence:a", "evidence:a"],
        },
        status,
    )
    return version, module, screen


def test_minimum_package_route_hash_schema_and_read_only(session):
    version, _, screen = seed_screen(session)
    before = (set(session.new), set(session.dirty), set(session.deleted))
    first = ScreenEvidenceBuilder(session).build(version.id, screen.id)
    second = ScreenEvidenceBuilder(session).build_by_canonical_id(version.id, screen.canonical_id)
    assert first.screen_route == "/retenciones"
    assert first.evidence_ids == []
    assert "invalid_relation:evidence_reference" in first.warnings
    assert first.evidence_hash == second.evidence_hash
    assert first.fields == first.controls == first.tables == []
    assert "Pantalla: Retenciones" in first.main_content_text
    assert (set(session.new), set(session.dirty), set(session.deleted)) == before
    with pytest.raises(ValidationError):
        ScreenEvidencePackage.model_validate({**first.model_dump(), "unexpected": True})


def test_structural_elements_are_related_safe_deduplicated_and_ordered(session):
    version, _, screen = seed_screen(session)
    add_item(
        session,
        version,
        "field",
        "field:b",
        {
            "id": "field:b",
            "screen_id": screen.canonical_id,
            "label": "RUC",
            "input_type": "text",
            "required": True,
            "readonly": False,
            "value": "0701234567001",
            "selector": "#ruc",
        },
    )
    add_item(
        session,
        version,
        "field",
        "field:a",
        {
            "id": "field:a",
            "screen_id": screen.canonical_id,
            "label": " RUC ",
            "required": False,
            "readonly": True,
        },
    )
    add_item(
        session,
        version,
        "field",
        "field:sensitive",
        {"id": "field:sensitive", "screen_id": screen.canonical_id, "label": "0701234567001"},
    )
    add_item(
        session,
        version,
        "field",
        "field:pending",
        {"id": "field:pending", "screen_id": screen.canonical_id, "label": "Pendiente"},
        ReviewStatus.PENDING_REVIEW,
    )
    add_item(
        session,
        version,
        "link",
        "link:pending",
        {"id": "link:pending", "screen_id": screen.canonical_id, "label": "Link"},
        ReviewStatus.PENDING_REVIEW,
    )
    add_item(
        session,
        version,
        "control",
        "control:search",
        {
            "id": "control:search",
            "screen_id": screen.canonical_id,
            "label": "Buscar",
            "control_type": "button",
            "mutative": False,
            "safety_decision": "allow",
            "html": "<button>",
        },
    )
    table = add_item(
        session,
        version,
        "table",
        "table:retenciones",
        {
            "id": "table:retenciones",
            "screen_id": screen.canonical_id,
            "name": "Retenciones",
            "rows": [{"ruc": "0701234567001"}],
        },
    )
    add_item(
        session,
        version,
        "table_column",
        "column:ruc",
        {"id": "column:ruc", "table_id": table.canonical_id, "name": "RUC"},
    )
    package = ScreenEvidenceBuilder(session).build(version.id, screen.id)
    assert [field.field_id for field in package.fields] == ["field:a"]
    assert package.controls[0].label == "Buscar"
    assert package.tables[0].columns[0].label == "RUC"
    serialized = package.model_dump_json()
    assert "0701234567001" not in serialized
    assert (
        "selector" not in serialized and "<button>" not in serialized and "rows" not in serialized
    )
    assert any(value.startswith("excluded_sensitive:field:") for value in package.warnings)
    assert "0701234567001" not in " ".join(package.warnings)
    assert any(value.startswith("excluded_review_status:field:") for value in package.warnings)
    assert not any("link" in value for value in package.warnings)


def test_corrected_fields_move_between_screens_using_effective_relationship(session):
    version, module, screen_a = seed_screen(session)
    screen_b = add_item(
        session,
        version,
        "screen",
        "screen:other",
        {
            "id": "screen:other",
            "erp_id": version.erp_id,
            "module_id": module.canonical_id,
            "title": "Otra pantalla",
            "route": "/other",
        },
    )
    moved_out = add_item(
        session,
        version,
        "field",
        "field:moved-out",
        {"id": "field:moved-out", "screen_id": screen_a.canonical_id, "label": "Sale"},
    )
    add_correction(
        session, moved_out, {**moved_out.source_payload, "screen_id": screen_b.canonical_id}
    )
    moved_in = add_item(
        session,
        version,
        "field",
        "field:moved-in",
        {"id": "field:moved-in", "screen_id": screen_b.canonical_id, "label": "Entra"},
    )
    add_correction(
        session, moved_in, {**moved_in.source_payload, "screen_id": screen_a.canonical_id}
    )
    evidence = add_item(
        session,
        version,
        "evidence",
        "evidence:moved",
        {
            "id": "evidence:moved",
            "source_entity_id": screen_a.canonical_id,
        },
    )
    add_correction(
        session,
        evidence,
        {**evidence.source_payload, "source_entity_id": screen_b.canonical_id},
    )
    package_a = ScreenEvidenceBuilder(session).build(version.id, screen_a.id)
    package_b = ScreenEvidenceBuilder(session).build(version.id, screen_b.id)
    assert [field.field_id for field in package_a.fields] == ["field:moved-in"]
    assert [field.field_id for field in package_b.fields] == ["field:moved-out"]
    assert "evidence:moved" not in package_a.evidence_ids
    assert "evidence:moved" in package_b.evidence_ids
    assert package_a.evidence_hash != package_b.evidence_hash


def test_corrected_tables_columns_states_events_and_transitions_use_effective_links(session):
    version, module, screen_a = seed_screen(session)
    screen_b = add_item(
        session,
        version,
        "screen",
        "screen:other",
        {
            "id": "screen:other",
            "erp_id": version.erp_id,
            "module_id": module.canonical_id,
            "title": "Otra",
            "route": "/other",
        },
    )
    table_a = add_item(
        session,
        version,
        "table",
        "table:a",
        {"id": "table:a", "screen_id": screen_a.canonical_id, "name": "A"},
    )
    table_b = add_item(
        session,
        version,
        "table",
        "table:b",
        {"id": "table:b", "screen_id": screen_b.canonical_id, "name": "B"},
    )
    add_correction(session, table_a, {**table_a.source_payload, "screen_id": screen_b.canonical_id})
    column = add_item(
        session,
        version,
        "table_column",
        "column:moved",
        {"id": "column:moved", "table_id": table_a.canonical_id, "name": "Movida"},
    )
    add_correction(session, column, {**column.source_payload, "table_id": table_b.canonical_id})
    state_a = add_item(
        session,
        version,
        "ui_state",
        "state:a",
        {"id": "state:a", "screen_id": screen_a.canonical_id, "title": "A"},
    )
    state_b = add_item(
        session,
        version,
        "ui_state",
        "state:b",
        {"id": "state:b", "screen_id": screen_b.canonical_id, "title": "B"},
    )
    add_correction(session, state_a, {**state_a.source_payload, "screen_id": screen_b.canonical_id})
    control_b = add_item(
        session,
        version,
        "control",
        "control:b",
        {"id": "control:b", "screen_id": screen_b.canonical_id, "label": "B", "mutative": False},
    )
    event = add_item(
        session,
        version,
        "event",
        "event:moved",
        {
            "id": "event:moved",
            "screen_id": screen_a.canonical_id,
            "source_state_id": state_a.canonical_id,
            "label": "Mover",
            "category": "state",
            "policy_decision": "allow",
        },
    )
    add_correction(
        session,
        event,
        {
            **event.source_payload,
            "screen_id": screen_b.canonical_id,
            "source_state_id": state_b.canonical_id,
        },
    )
    transition = add_item(
        session,
        version,
        "transition",
        "transition:moved",
        {
            "id": "transition:moved",
            "source_state_id": "state:outside-a",
            "target_state_id": "state:outside-b",
            "category": "state",
        },
    )
    add_correction(
        session,
        transition,
        {
            **transition.source_payload,
            "source_state_id": state_a.canonical_id,
            "target_state_id": state_b.canonical_id,
            "trigger_control_id": control_b.canonical_id,
        },
    )
    package_a = ScreenEvidenceBuilder(session).build(version.id, screen_a.id)
    package_b = ScreenEvidenceBuilder(session).build(version.id, screen_b.id)
    assert package_a.tables == [] and package_a.ui_states == [] and package_a.events == []
    tables = {table.table_id: table for table in package_b.tables}
    assert tables[table_a.canonical_id].columns == []
    assert tables[table_b.canonical_id].columns[0].column_id == column.canonical_id
    assert {state.state_id for state in package_b.ui_states} == {
        state_a.canonical_id,
        state_b.canonical_id,
    }
    assert package_b.events[0].event_id == event.canonical_id
    assert package_b.transitions[0].trigger_control_id == control_b.canonical_id


def test_unrelated_inconsistent_corrected_is_ignored_but_related_one_fails(session):
    version, module, screen_a = seed_screen(session)
    screen_b = add_item(
        session,
        version,
        "screen",
        "screen:other",
        {
            "id": "screen:other",
            "erp_id": version.erp_id,
            "module_id": module.canonical_id,
            "title": "Otra",
            "route": "/other",
        },
    )
    unrelated = add_item(
        session,
        version,
        "field",
        "field:unrelated",
        {"id": "field:unrelated", "screen_id": screen_b.canonical_id, "label": "Ajeno"},
        ReviewStatus.CORRECTED,
    )
    assert unrelated
    package = ScreenEvidenceBuilder(session).build(version.id, screen_a.id)
    assert package.fields == []
    assert "ignored_inconsistent_corrected:field" in package.warnings
    related = add_item(
        session,
        version,
        "field",
        "field:related",
        {"id": "field:related", "screen_id": screen_a.canonical_id, "label": "Relacionado"},
        ReviewStatus.CORRECTED,
    )
    assert related
    with pytest.raises(EffectiveContentIntegrityError):
        ScreenEvidenceBuilder(session).build(version.id, screen_a.id)


def test_states_events_transitions_and_invalid_reference_warning(session):
    version, _, screen = seed_screen(session)
    state = add_item(
        session,
        version,
        "ui_state",
        "state:root",
        {"id": "state:root", "screen_id": screen.canonical_id, "title": "Inicio", "depth": 0},
    )
    add_item(
        session,
        version,
        "event",
        "event:search",
        {
            "id": "event:search",
            "screen_id": screen.canonical_id,
            "source_state_id": state.canonical_id,
            "label": "Buscar",
            "category": "query",
            "policy_decision": "allow",
            "mutative": False,
        },
    )
    add_item(
        session,
        version,
        "transition",
        "transition:search",
        {
            "id": "transition:search",
            "source_state_id": state.canonical_id,
            "target_state_id": "state:missing",
            "category": "state_change",
        },
    )
    package = ScreenEvidenceBuilder(session).build(version.id, screen.id)
    assert package.ui_states[0].state_id == state.canonical_id
    assert package.events[0].category == "query"
    assert package.transitions == []
    assert any("invalid_relation" in warning for warning in package.warnings)


def test_corrected_payload_wins_and_inconsistent_corrected_fails(session):
    version, _, screen = seed_screen(session)
    field = add_item(
        session,
        version,
        "field",
        "field:corrected",
        {"id": "field:corrected", "screen_id": screen.canonical_id, "label": "Original"},
        ReviewStatus.CORRECTED,
    )
    corrected = {"id": field.canonical_id, "screen_id": screen.canonical_id, "label": "Corregido"}
    session.add(
        ReviewAction(
            knowledge_item_id=field.id,
            action=ReviewActionType.CORRECT,
            previous_status=ReviewStatus.PENDING_REVIEW,
            new_status=ReviewStatus.CORRECTED,
            corrected_payload=corrected,
            review_notes="synthetic",
            reviewer_subject="test",
            item_content_hash=field.content_hash,
            source=ReviewSource.CLI,
        )
    )
    session.flush()
    assert (
        ScreenEvidenceBuilder(session).build(version.id, screen.id).fields[0].label == "Corregido"
    )
    broken = add_item(
        session,
        version,
        "control",
        "control:broken",
        {"id": "control:broken", "screen_id": screen.canonical_id, "label": "Broken"},
        ReviewStatus.CORRECTED,
    )
    assert broken
    with pytest.raises(EffectiveContentIntegrityError):
        ScreenEvidenceBuilder(session).build(version.id, screen.id)


def test_screen_validation_and_unsafe_routes(session):
    version, _, screen = seed_screen(session)
    other = seed_version(session)
    with pytest.raises(EvidenceVersionMismatchError):
        ScreenEvidenceBuilder(session).build(other.id, screen.id)
    field = add_item(
        session,
        version,
        "field",
        "field:not-screen",
        {"id": "field:not-screen", "screen_id": screen.canonical_id, "label": "X"},
    )
    with pytest.raises(EvidenceEntityTypeError):
        ScreenEvidenceBuilder(session).build(version.id, field.id)
    screen.current_review_status = ReviewStatus.REJECTED
    with pytest.raises(EvidenceScreenReviewError):
        ScreenEvidenceBuilder(session).build(version.id, screen.id)
    session.rollback()
    unsafe_version, _, unsafe_screen = seed_screen(session, route="javascript:alert(1)")
    with pytest.raises(UnsafeScreenRouteError):
        ScreenEvidenceBuilder(session).build(unsafe_version.id, unsafe_screen.id)


def test_limits_hash_changes_and_insertion_order_is_irrelevant(session):
    version, _, screen = seed_screen(session)
    for index in reversed(range(52)):
        add_item(
            session,
            version,
            "field",
            f"field:{index:03}",
            {
                "id": f"field:{index:03}",
                "screen_id": screen.canonical_id,
                "label": f"Campo {index:03}",
            },
        )
    first = ScreenEvidenceBuilder(session).build(version.id, screen.id)
    second = ScreenEvidenceBuilder(session).build(version.id, screen.id)
    assert len(first.fields) == 50
    assert first.evidence_hash == second.evidence_hash
    assert first.fields == sorted(first.fields, key=lambda value: value.field_id)
    assert "limit_exceeded:fields" in first.warnings
    add_item(
        session,
        version,
        "control",
        "control:new",
        {
            "id": "control:new",
            "screen_id": screen.canonical_id,
            "label": "Nuevo",
            "mutative": False,
        },
    )
    changed = ScreenEvidenceBuilder(session).build(version.id, screen.id)
    assert changed.evidence_hash != first.evidence_hash
