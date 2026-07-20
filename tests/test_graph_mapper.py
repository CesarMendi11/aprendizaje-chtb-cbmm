import pytest

from src.graph.mapper import LABELS, GraphMapper, GraphMappingError
from src.graph.projection_service import GraphProjectionService

PAYLOADS = {
    "erp_system": {
        "id": "erp:synthetic",
        "slug": "synthetic",
        "name": "Synthetic ERP",
        "profile_name": "test",
    },
    "module": {
        "id": "module:one",
        "erp_id": "erp:synthetic",
        "name": "Inventory",
        "normalized_name": "inventory",
    },
    "screen": {
        "id": "screen:one",
        "erp_id": "erp:synthetic",
        "module_id": "module:one",
        "title": "Products",
        "normalized_title": "products",
        "route": "/app/products",
    },
    "ui_state": {
        "id": "state:one",
        "screen_id": "screen:one",
        "route": "/app/products",
        "title": "Products",
        "structural_fingerprint": "safe",
        "depth": 0,
    },
    "field": {
        "id": "field:one",
        "screen_id": "screen:one",
        "label": "Reference",
        "normalized_label": "reference",
        "required": False,
    },
    "control": {
        "id": "control:one",
        "screen_id": "screen:one",
        "label": "Search",
        "normalized_label": "search",
        "control_type": "button",
        "mutative": False,
    },
    "table": {
        "id": "table:one",
        "screen_id": "screen:one",
        "name": "Products",
        "normalized_name": "products",
    },
    "table_column": {
        "id": "column:one",
        "table_id": "table:one",
        "name": "Name",
        "normalized_name": "name",
        "position": 0,
    },
    "link": {
        "id": "link:one",
        "screen_id": "screen:one",
        "label": "Details",
        "normalized_label": "details",
        "target_route": "/app/details",
        "target_screen_id": "screen:two",
    },
    "event": {
        "id": "event:one",
        "screen_id": "screen:one",
        "source_state_id": "state:one",
        "label": "Open",
        "normalized_label": "open",
        "category": "navigation",
        "policy_decision": "allow",
    },
    "transition": {
        "id": "transition:one",
        "source_state_id": "state:one",
        "target_state_id": "state:two",
        "event_id": "event:one",
        "category": "navigation",
        "changed": True,
        "route_changed": False,
    },
    "evidence": {
        "id": "evidence:one",
        "evidence_type": "structural_json",
        "artifact_path": "data/processed/structural/screen_index.json",
        "source_entity_type": "screen",
        "source_entity_id": "screen:one",
    },
}


@pytest.mark.parametrize("entity_type", sorted(PAYLOADS))
def test_mapper_supports_each_canonical_entity_with_whitelist(entity_type):
    node = GraphMapper().map_node(
        entity_type=entity_type,
        payload=PAYLOADS[entity_type],
        content_hash="a" * 64,
        review_status="approved",
        erp_id="erp:synthetic",
        knowledge_version="version-one",
        projected_at="2026-01-01T00:00:00+00:00",
    )
    assert node.label == LABELS[entity_type]
    assert node.properties["managed_by"] == "erp_assistant"
    assert "metadata" not in node.properties and "source_payload" not in node.properties
    assert all(not isinstance(value, (dict, list)) for value in node.properties.values())


def test_mapper_rejects_unknown_types_and_sensitive_properties():
    with pytest.raises(GraphMappingError, match="no soportado"):
        GraphMapper().map_node(
            entity_type="unknown",
            payload={"id": "x"},
            content_hash="x",
            review_status="approved",
            erp_id="erp:x",
            knowledge_version="v",
            projected_at="now",
        )
    payload = {**PAYLOADS["screen"], "title": "001-001-000000001"}
    with pytest.raises(GraphMappingError, match="sensible"):
        GraphMapper().map_node(
            entity_type="screen",
            payload=payload,
            content_hash="x",
            review_status="approved",
            erp_id="erp:x",
            knowledge_version="v",
            projected_at="now",
        )
    with pytest.raises(GraphMappingError, match="no elegible"):
        GraphMapper().map_node(
            entity_type="screen",
            payload=PAYLOADS["screen"],
            content_hash="x",
            review_status="pending_review",
            erp_id="erp:x",
            knowledge_version="v",
            projected_at="now",
        )


def test_projection_is_deterministic_and_omits_missing_endpoints():
    entries = [
        {"entity_type": key, "payload": value, "content_hash": key, "review_status": "approved"}
        for key, value in PAYLOADS.items()
    ]
    service = GraphProjectionService()
    first = service.build_plan(
        entries,
        erp_id="erp:synthetic",
        knowledge_version="v1",
        projected_at="2026-01-01T00:00:00+00:00",
    )
    second = service.build_plan(
        entries,
        erp_id="erp:synthetic",
        knowledge_version="v1",
        projected_at="2026-01-01T00:00:00+00:00",
    )
    assert first == second
    assert first.skipped_relationships > 0
    assert first.skipped_reasons == {"endpoint_not_projected": first.skipped_relationships}
    assert all(
        rel.source_key in {node.key for node in first.nodes}
        and rel.target_key in {node.key for node in first.nodes}
        for rel in first.relationships
    )
