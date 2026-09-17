from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

STRUCTURAL_COLLECTIONS: dict[str, str] = {
    "modules": "module",
    "screens": "screen",
    "ui_states": "ui_state",
    "fields": "field",
    "controls": "control",
    "tables": "table",
    "table_columns": "table_column",
    "links": "link",
    "events": "event",
    "transitions": "transition",
}

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _issue(
    issues: list[dict[str, Any]],
    counts: Counter[str],
    code: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    source_ref: str | None = None,
    evidence_id: str | None = None,
) -> None:
    counts[code] += 1
    row: dict[str, Any] = {"code": code}
    if entity_type is not None:
        row["entity_type"] = entity_type
    if entity_id is not None:
        row["entity_id"] = entity_id
    if source_ref is not None:
        row["source_ref"] = source_ref
    if evidence_id is not None:
        row["evidence_id"] = evidence_id
    issues.append(row)


def audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()

    source_artifacts = payload.get("source_artifacts") or []
    source_hashes = payload.get("source_artifact_hashes") or {}
    evidence_rows = payload.get("evidence") or []

    if not isinstance(source_artifacts, list):
        raise ValueError("source_artifacts must be a list")
    if not isinstance(source_hashes, dict):
        raise ValueError("source_artifact_hashes must be an object")
    if not isinstance(evidence_rows, list):
        raise ValueError("evidence must be a list")

    source_artifact_set = {str(value) for value in source_artifacts}
    source_hash_keys = {str(value) for value in source_hashes}

    if source_artifact_set != source_hash_keys:
        for ref in sorted(source_artifact_set - source_hash_keys):
            _issue(issues, issue_counts, "source_artifact_missing_hash", source_ref=ref)
        for ref in sorted(source_hash_keys - source_artifact_set):
            _issue(issues, issue_counts, "source_hash_missing_artifact", source_ref=ref)

    invalid_registry_hashes = 0
    for ref, value in source_hashes.items():
        if not isinstance(value, str) or HASH_RE.fullmatch(value) is None:
            invalid_registry_hashes += 1
            _issue(
                issues,
                issue_counts,
                "invalid_source_artifact_hash",
                source_ref=str(ref),
            )

    evidence_by_id: dict[str, dict[str, Any]] = {}
    duplicate_evidence_ids = 0
    for row in evidence_rows:
        if not isinstance(row, dict):
            _issue(issues, issue_counts, "invalid_evidence_record")
            continue
        evidence_id = str(row.get("id") or "")
        if not evidence_id:
            _issue(issues, issue_counts, "missing_evidence_id")
            continue
        if evidence_id in evidence_by_id:
            duplicate_evidence_ids += 1
            _issue(
                issues,
                issue_counts,
                "duplicate_evidence_id",
                evidence_id=evidence_id,
            )
            continue
        evidence_by_id[evidence_id] = row

    entities: dict[tuple[str, str], dict[str, Any]] = {}
    collection_metrics: dict[str, dict[str, Any]] = {}

    total_entities = 0
    entities_with_source_refs = 0
    entities_with_all_source_refs_resolved = 0
    source_refs_total = 0
    source_refs_resolved = 0
    evidence_ids_total = 0
    evidence_ids_resolved = 0
    entities_with_evidence_ids = 0
    entities_with_all_evidence_ids_resolved = 0

    for collection, entity_type in STRUCTURAL_COLLECTIONS.items():
        rows = payload.get(collection) or []
        if not isinstance(rows, list):
            raise ValueError(f"{collection} must be a list")

        local = {
            "entities": len(rows),
            "with_source_refs": 0,
            "all_source_refs_resolved": 0,
            "with_evidence_ids": 0,
            "all_evidence_ids_resolved": 0,
        }

        for row in rows:
            if not isinstance(row, dict):
                _issue(
                    issues,
                    issue_counts,
                    "invalid_entity_record",
                    entity_type=entity_type,
                )
                continue

            entity_id = str(row.get("id") or "")
            if not entity_id:
                _issue(
                    issues,
                    issue_counts,
                    "missing_entity_id",
                    entity_type=entity_type,
                )
                continue

            entities[(entity_type, entity_id)] = row
            total_entities += 1

            refs = row.get("source_refs") or []
            if not isinstance(refs, list):
                refs = []
                _issue(
                    issues,
                    issue_counts,
                    "invalid_source_refs",
                    entity_type=entity_type,
                    entity_id=entity_id,
                )

            if refs:
                entities_with_source_refs += 1
                local["with_source_refs"] += 1
            else:
                _issue(
                    issues,
                    issue_counts,
                    "missing_source_refs",
                    entity_type=entity_type,
                    entity_id=entity_id,
                )

            refs_ok = bool(refs)
            for ref_raw in refs:
                ref = str(ref_raw)
                source_refs_total += 1
                if ref in source_artifact_set and ref in source_hash_keys:
                    source_refs_resolved += 1
                else:
                    refs_ok = False
                    _issue(
                        issues,
                        issue_counts,
                        "unresolved_source_ref",
                        entity_type=entity_type,
                        entity_id=entity_id,
                        source_ref=ref,
                    )
            if refs_ok:
                entities_with_all_source_refs_resolved += 1
                local["all_source_refs_resolved"] += 1

            declared_evidence_ids = row.get("evidence_ids")
            if declared_evidence_ids is None:
                declared_evidence_ids = []
            if not isinstance(declared_evidence_ids, list):
                declared_evidence_ids = []
                _issue(
                    issues,
                    issue_counts,
                    "invalid_evidence_ids",
                    entity_type=entity_type,
                    entity_id=entity_id,
                )

            if declared_evidence_ids:
                entities_with_evidence_ids += 1
                local["with_evidence_ids"] += 1
                evidence_ok = True
                for evidence_id_raw in declared_evidence_ids:
                    evidence_id = str(evidence_id_raw)
                    evidence_ids_total += 1
                    if evidence_id in evidence_by_id:
                        evidence_ids_resolved += 1
                    else:
                        evidence_ok = False
                        _issue(
                            issues,
                            issue_counts,
                            "unresolved_evidence_id",
                            entity_type=entity_type,
                            entity_id=entity_id,
                            evidence_id=evidence_id,
                        )
                if evidence_ok:
                    entities_with_all_evidence_ids_resolved += 1
                    local["all_evidence_ids_resolved"] += 1

        collection_metrics[collection] = local

    evidence_artifact_refs_resolved = 0
    evidence_hashes_valid = 0
    evidence_structural_hash_matches = 0
    evidence_structural_total = 0
    evidence_source_entities_resolved = 0

    for evidence_id, row in evidence_by_id.items():
        artifact_path = str(row.get("artifact_path") or "")
        artifact_ref = Path(artifact_path).name if artifact_path else ""

        if artifact_ref and artifact_ref in source_artifact_set and artifact_ref in source_hash_keys:
            evidence_artifact_refs_resolved += 1
        else:
            _issue(
                issues,
                issue_counts,
                "unresolved_evidence_artifact",
                source_ref=artifact_ref or artifact_path,
                evidence_id=evidence_id,
            )

        artifact_hash = row.get("artifact_hash")
        valid_hash = isinstance(artifact_hash, str) and HASH_RE.fullmatch(artifact_hash) is not None
        if valid_hash:
            evidence_hashes_valid += 1
        else:
            _issue(
                issues,
                issue_counts,
                "invalid_evidence_artifact_hash",
                evidence_id=evidence_id,
            )

        evidence_type = str(row.get("evidence_type") or "")
        if evidence_type == "structural_json":
            evidence_structural_total += 1
            expected = source_hashes.get(artifact_ref)
            if valid_hash and expected == artifact_hash:
                evidence_structural_hash_matches += 1
            else:
                _issue(
                    issues,
                    issue_counts,
                    "structural_evidence_hash_mismatch",
                    source_ref=artifact_ref,
                    evidence_id=evidence_id,
                )

        source_entity_type = str(row.get("source_entity_type") or "")
        source_entity_id = str(row.get("source_entity_id") or "")
        if (source_entity_type, source_entity_id) in entities:
            evidence_source_entities_resolved += 1
        else:
            _issue(
                issues,
                issue_counts,
                "unresolved_evidence_source_entity",
                entity_type=source_entity_type or None,
                entity_id=source_entity_id or None,
                evidence_id=evidence_id,
            )

    issue_count = len(issues)
    status = "passed" if issue_count == 0 else "failed"

    def rate(numerator: int, denominator: int) -> float | None:
        if denominator == 0:
            return None
        return numerator / denominator

    return {
        "schema_version": "1.0.0",
        "audit_type": "deterministic_evidence_traceability",
        "status": status,
        "knowledge_version": payload.get("knowledge_version"),
        "universe": {
            "collections": list(STRUCTURAL_COLLECTIONS),
            "structural_entities": total_entities,
            "evidence_records": len(evidence_by_id),
            "erp_system_excluded_from_entity_denominator": True,
            "evidence_records_audited_separately": True,
        },
        "metrics": {
            "entity_source_traceability": {
                "numerator": entities_with_all_source_refs_resolved,
                "denominator": total_entities,
                "rate": rate(entities_with_all_source_refs_resolved, total_entities),
            },
            "entity_source_ref_presence": {
                "numerator": entities_with_source_refs,
                "denominator": total_entities,
                "rate": rate(entities_with_source_refs, total_entities),
            },
            "source_ref_resolution": {
                "numerator": source_refs_resolved,
                "denominator": source_refs_total,
                "rate": rate(source_refs_resolved, source_refs_total),
            },
            "declared_evidence_id_resolution": {
                "numerator": evidence_ids_resolved,
                "denominator": evidence_ids_total,
                "rate": rate(evidence_ids_resolved, evidence_ids_total),
            },
            "entities_with_direct_evidence_ids": {
                "numerator": entities_with_evidence_ids,
                "denominator": total_entities,
                "rate": rate(entities_with_evidence_ids, total_entities),
                "interpretation": "descriptive only; evidence_ids are optional for some canonical entity types",
            },
            "entities_with_all_declared_evidence_ids_resolved": {
                "numerator": entities_with_all_evidence_ids_resolved,
                "denominator": entities_with_evidence_ids,
                "rate": rate(
                    entities_with_all_evidence_ids_resolved,
                    entities_with_evidence_ids,
                ),
            },
            "evidence_artifact_resolution": {
                "numerator": evidence_artifact_refs_resolved,
                "denominator": len(evidence_by_id),
                "rate": rate(evidence_artifact_refs_resolved, len(evidence_by_id)),
            },
            "evidence_hash_validity": {
                "numerator": evidence_hashes_valid,
                "denominator": len(evidence_by_id),
                "rate": rate(evidence_hashes_valid, len(evidence_by_id)),
            },
            "structural_evidence_container_hash_match": {
                "numerator": evidence_structural_hash_matches,
                "denominator": evidence_structural_total,
                "rate": rate(evidence_structural_hash_matches, evidence_structural_total),
                "interpretation": (
                    "Exact equality is required only for structural_json evidence. "
                    "network_trace evidence hashes a sanitized per-screen observation subset "
                    "while source_artifact_hashes stores the full network-evidence container hash."
                ),
            },
            "evidence_source_entity_resolution": {
                "numerator": evidence_source_entities_resolved,
                "denominator": len(evidence_by_id),
                "rate": rate(evidence_source_entities_resolved, len(evidence_by_id)),
            },
            "source_artifact_registry": {
                "artifacts": len(source_artifact_set),
                "hash_entries": len(source_hash_keys),
                "invalid_hashes": invalid_registry_hashes,
            },
            "duplicate_evidence_ids": duplicate_evidence_ids,
        },
        "by_collection": collection_metrics,
        "issue_count": issue_count,
        "issue_counts": dict(sorted(issue_counts.items())),
        "issues": issues,
        "pass_criteria": {
            "issue_count_must_equal_zero": True,
            "all_structural_entities_must_have_resolved_source_refs": True,
            "all_declared_evidence_ids_must_resolve": True,
            "all_evidence_artifacts_must_resolve": True,
            "all_evidence_hashes_must_be_valid_sha256": True,
            "all_structural_json_evidence_hashes_must_match_source_registry": True,
            "all_evidence_source_entities_must_resolve": True,
        },
    }


def audit_knowledge_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("knowledge.json must contain an object")
    report = audit_payload(payload)
    report["knowledge_file"] = {
        "path": str(path),
        "sha256": _sha256(path),
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    path = Path(args.knowledge)
    report = audit_knowledge_file(path)

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")

    metrics = report["metrics"]
    print(
        "Evidence traceability:"
        f" status={report['status']}"
        f" entities={report['universe']['structural_entities']}"
        f" entity_source={metrics['entity_source_traceability']['numerator']}/"
        f"{metrics['entity_source_traceability']['denominator']}"
        f" evidence={report['universe']['evidence_records']}"
        f" issues={report['issue_count']}"
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
