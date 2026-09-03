from __future__ import annotations

import json

import pytest

from erp_assistant.structural.canonical import (
    CanonicalKnowledgeBuilder,
    CanonicalNetworkEvidenceError,
    CanonicalNetworkEvidenceIntegrator,
)
from tests.fixtures.canonical import fictional_artifacts, fictional_profile


def _knowledge():
    return CanonicalKnowledgeBuilder().build(
        fictional_profile(),
        fictional_artifacts(),
    )


def test_integrator_attaches_network_trace_without_changing_knowledge_version(tmp_path):
    knowledge = _knowledge()
    path = tmp_path / "network_evidence.json"
    path.write_text(
        json.dumps(
            {
                "capture_policy": {
                    "bodies_captured": False,
                    "headers_captured": False,
                    "query_values_captured": False,
                    "resource_types": ["xhr"],
                },
                "observations": [
                    {
                        "screen_route": "/app/inventory/products",
                        "method": "GET",
                        "endpoint_path": "/api/products/{id}",
                        "origin_id": "same_origin",
                        "origin_kind": "same_origin",
                        "resource_type": "xhr",
                        "query_keys": ["page", "status"],
                        "status_codes": [200],
                        "observed_count": 3,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CanonicalNetworkEvidenceIntegrator(tmp_path).integrate(
        knowledge,
        path,
    )

    assert result.knowledge.knowledge_version == knowledge.knowledge_version
    assert result.observation_count == 3
    assert result.screen_count == 1
    assert result.omitted_observations == 0

    products = next(
        item for item in result.knowledge.screens if item.route == "/app/inventory/products"
    )
    network = [
        item for item in result.knowledge.evidence if item.evidence_type.value == "network_trace"
    ]
    assert len(network) == 1
    evidence = network[0]
    assert evidence.source_entity_id == products.id
    assert evidence.id in products.evidence_ids
    assert "network_evidence.json" in products.source_refs
    assert evidence.metadata["endpoint_paths"] == "/api/products/{id}"
    assert evidence.metadata["headers_captured"] is False
    assert evidence.metadata["bodies_captured"] is False
    assert evidence.metadata["query_values_captured"] is False
    assert "network_evidence.json" in result.knowledge.source_artifacts


def test_integrator_rejects_unsafe_capture_policy(tmp_path):
    path = tmp_path / "network_evidence.json"
    path.write_text(
        json.dumps(
            {
                "capture_policy": {
                    "bodies_captured": True,
                    "headers_captured": False,
                    "query_values_captured": False,
                },
                "observations": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(CanonicalNetworkEvidenceError, match="insegura"):
        CanonicalNetworkEvidenceIntegrator(tmp_path).integrate(
            _knowledge(),
            path,
        )


def test_integrator_omits_sensitive_observation(tmp_path):
    path = tmp_path / "network_evidence.json"
    path.write_text(
        json.dumps(
            {
                "capture_policy": {
                    "bodies_captured": False,
                    "headers_captured": False,
                    "query_values_captured": False,
                },
                "observations": [
                    {
                        "screen_route": "/app/inventory/products",
                        "method": "GET",
                        "endpoint_path": "/api/users/owner@example.test",
                        "origin_id": "same_origin",
                        "origin_kind": "same_origin",
                        "resource_type": "xhr",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CanonicalNetworkEvidenceIntegrator(tmp_path).integrate(
        _knowledge(),
        path,
    )

    assert result.observation_count == 0
    assert result.screen_count == 0
    assert result.sensitive_exclusions == 1
    assert result.omitted_observations == 1


def test_integrator_attaches_dynamic_state_route_trace_to_owning_screen(tmp_path):
    artifacts = fictional_artifacts()
    artifacts["state_registry.json"]["states"].append(
        {
            "state_id": "raw:product-create",
            "route": "/app/inventory/products/create",
            "title": "Create product",
            "structural_signature": "product-create",
            "path": {
                "root_state_id": "raw:product",
                "target_state_id": "raw:product-create",
                "depth": 1,
                "steps": [],
            },
            "summary": {
                "inputs": [{"label": "Name", "name": "name"}],
                "buttons": [],
                "tables": [],
                "links": [],
            },
        }
    )
    artifacts["state_flow_graph.json"]["transitions"].append(
        {
            "source_state_id": "raw:product",
            "target_state_id": "raw:product-create",
            "event": {
                "event_type": "mutative_action",
                "label": "New",
                "decision": "deny",
            },
            "changed_route": True,
            "observed": True,
            "metadata": {"effect": "ROUTE_CHANGE"},
        }
    )
    knowledge = CanonicalKnowledgeBuilder().build(fictional_profile(), artifacts)
    products = next(
        item for item in knowledge.screens if item.route == "/app/inventory/products"
    )

    path = tmp_path / "network_evidence.json"
    path.write_text(
        json.dumps(
            {
                "capture_policy": {
                    "bodies_captured": False,
                    "headers_captured": False,
                    "query_values_captured": False,
                },
                "observations": [
                    {
                        "screen_route": "/app/inventory/products/create",
                        "method": "GET",
                        "endpoint_path": "/api/products/template",
                        "origin_id": "same_origin",
                        "origin_kind": "same_origin",
                        "resource_type": "xhr",
                        "status_codes": [200],
                        "observed_count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = CanonicalNetworkEvidenceIntegrator(tmp_path).integrate(knowledge, path)

    assert result.observation_count == 2
    assert result.screen_count == 1
    assert result.omitted_observations == 0
    evidence = [
        item
        for item in result.knowledge.evidence
        if item.evidence_type.value == "network_trace"
    ]
    assert len(evidence) == 1
    assert evidence[0].source_entity_id == products.id
    assert evidence[0].id in next(
        item
        for item in result.knowledge.screens
        if item.id == products.id
    ).evidence_ids
