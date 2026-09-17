from __future__ import annotations

import copy

from scripts.audit.audit_evidence_traceability import audit_payload


def _hash(char: str) -> str:
    return char * 64


def _payload() -> dict:
    return {
        "knowledge_version": "test-v1",
        "source_artifacts": [
            "profile:configs/cbmm.yaml",
            "routes_graph.json",
            "screen_index.json",
            "state_flow_graph.json",
            "network_evidence.json",
        ],
        "source_artifact_hashes": {
            "profile:configs/cbmm.yaml": _hash("a"),
            "routes_graph.json": _hash("b"),
            "screen_index.json": _hash("c"),
            "state_flow_graph.json": _hash("d"),
            "network_evidence.json": _hash("e"),
        },
        "modules": [
            {
                "id": "module:m1",
                "source_refs": ["routes_graph.json"],
                "evidence_ids": ["evidence:module"],
            }
        ],
        "screens": [
            {
                "id": "screen:s1",
                "source_refs": ["screen_index.json", "network_evidence.json"],
                "evidence_ids": ["evidence:screen", "evidence:network"],
            }
        ],
        "ui_states": [
            {
                "id": "ui_state:u1",
                "source_refs": ["screen_index.json"],
                "evidence_ids": [],
            }
        ],
        "fields": [
            {
                "id": "field:f1",
                "source_refs": ["screen_index.json"],
                "evidence_ids": ["evidence:screen"],
            }
        ],
        "controls": [],
        "tables": [],
        "table_columns": [
            {
                "id": "table_column:c1",
                "source_refs": ["screen_index.json"],
            }
        ],
        "links": [],
        "events": [
            {
                "id": "event:e1",
                "source_refs": ["state_flow_graph.json"],
                "evidence_ids": [],
            }
        ],
        "transitions": [],
        "evidence": [
            {
                "id": "evidence:module",
                "evidence_type": "structural_json",
                "artifact_path": "data/processed/structural/routes_graph.json",
                "artifact_hash": _hash("b"),
                "source_entity_type": "module",
                "source_entity_id": "module:m1",
            },
            {
                "id": "evidence:screen",
                "evidence_type": "structural_json",
                "artifact_path": "data/processed/structural/screen_index.json",
                "artifact_hash": _hash("c"),
                "source_entity_type": "screen",
                "source_entity_id": "screen:s1",
            },
            {
                "id": "evidence:network",
                "evidence_type": "network_trace",
                "artifact_path": "data/processed/structural/network_evidence.json",
                "artifact_hash": _hash("f"),
                "source_entity_type": "screen",
                "source_entity_id": "screen:s1",
            },
        ],
    }


def test_traceability_audit_passes_resolved_provenance():
    report = audit_payload(_payload())

    assert report["status"] == "passed"
    assert report["issue_count"] == 0
    assert report["metrics"]["entity_source_traceability"]["rate"] == 1.0
    assert report["metrics"]["source_ref_resolution"]["rate"] == 1.0
    assert report["metrics"]["declared_evidence_id_resolution"]["rate"] == 1.0
    assert report["metrics"]["structural_evidence_container_hash_match"]["rate"] == 1.0


def test_traceability_audit_fails_missing_source_ref():
    payload = _payload()
    payload["events"][0]["source_refs"] = []

    report = audit_payload(payload)

    assert report["status"] == "failed"
    assert report["issue_counts"]["missing_source_refs"] == 1


def test_traceability_audit_fails_unresolved_declared_evidence_id():
    payload = _payload()
    payload["screens"][0]["evidence_ids"].append("evidence:missing")

    report = audit_payload(payload)

    assert report["status"] == "failed"
    assert report["issue_counts"]["unresolved_evidence_id"] == 1


def test_traceability_audit_fails_structural_hash_mismatch():
    payload = _payload()
    payload["evidence"][0]["artifact_hash"] = _hash("9")

    report = audit_payload(payload)

    assert report["status"] == "failed"
    assert report["issue_counts"]["structural_evidence_hash_mismatch"] == 1


def test_traceability_audit_accepts_network_subset_hash_distinct_from_container_hash():
    payload = _payload()
    assert (
        payload["evidence"][2]["artifact_hash"]
        != payload["source_artifact_hashes"]["network_evidence.json"]
    )

    report = audit_payload(payload)

    assert report["status"] == "passed"


def test_traceability_audit_fails_orphan_evidence_source_entity():
    payload = copy.deepcopy(_payload())
    payload["evidence"][0]["source_entity_id"] = "module:missing"

    report = audit_payload(payload)

    assert report["status"] == "failed"
    assert report["issue_counts"]["unresolved_evidence_source_entity"] == 1
