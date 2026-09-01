from __future__ import annotations

from erp_assistant.semantic.evidence.network_trace import safe_network_trace


def payload(**metadata_updates):
    metadata = {
        "observation_count": 3,
        "endpoint_count": 2,
        "endpoint_paths": "/api/catalogos | /api/retenciones/{id}",
        "methods": "GET,HEAD",
        "resource_types": "fetch,xhr",
        "origin_kinds": "same_origin",
        "query_keys": "estado,fecha",
        "status_codes": "200,304",
        "headers_captured": False,
        "bodies_captured": False,
        "query_values_captured": False,
    }
    metadata.update(metadata_updates)
    return {
        "id": "evidence:network",
        "evidence_type": "network_trace",
        "artifact_path": "network/network_evidence.json",
        "source_entity_type": "screen",
        "source_entity_id": "screen:test",
        "metadata": metadata,
    }


def test_safe_network_trace_projects_current_canonical_contract():
    trace = safe_network_trace("evidence:network", payload())

    assert trace is not None
    assert trace.evidence_id == "evidence:network"
    assert trace.methods == ("GET", "HEAD")
    assert trace.endpoint_paths == ("/api/catalogos", "/api/retenciones/{id}")
    assert trace.resource_types == ("fetch", "xhr")
    assert trace.origin_kinds == ("same_origin",)
    assert trace.query_keys == ("estado", "fecha")
    assert trace.status_codes == (200, 304)
    assert trace.observation_count == 3
    assert trace.endpoint_count == 2
    assert trace.read_only is True


def test_safe_network_trace_keeps_mutative_methods_observational_only():
    trace = safe_network_trace(
        "evidence:network",
        payload(methods="GET,POST", observation_count=2),
    )

    assert trace is not None
    assert trace.methods == ("GET", "POST")
    assert trace.read_only is False


def test_safe_network_trace_fails_closed_without_safety_provenance():
    missing = payload()
    missing["metadata"].pop("headers_captured")

    assert safe_network_trace("evidence:network", missing) is None
    assert (
        safe_network_trace(
            "evidence:network",
            payload(headers_captured=True),
        )
        is None
    )
    assert (
        safe_network_trace(
            "evidence:network",
            payload(query_values_captured=True),
        )
        is None
    )


def test_safe_network_trace_rejects_unsafe_or_unknown_aggregate_metadata():
    assert (
        safe_network_trace(
            "evidence:network",
            payload(endpoint_paths="https://example.invalid/api/retenciones"),
        )
        is None
    )
    assert (
        safe_network_trace(
            "evidence:network",
            payload(endpoint_paths="/api/retenciones?token=secret"),
        )
        is None
    )
    assert (
        safe_network_trace(
            "evidence:network",
            payload(methods="GET,OTHER"),
        )
        is None
    )
    assert (
        safe_network_trace(
            "evidence:network",
            payload(origin_kinds="same_origin,unknown"),
        )
        is None
    )
