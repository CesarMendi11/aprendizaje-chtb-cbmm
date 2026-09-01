from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from erp_assistant.structural.canonical.privacy import (
    PERSISTED_DROP_KEYS,
    PERSISTED_FORBIDDEN_KEYS,
    PERSISTED_INTERNAL_STATE_FRAGMENT,
    PERSISTED_ROUTE_KEYS,
    PERSISTED_SELECTOR_KEYS,
    _is_safe_technical_token,
    _is_technical_persistence_key,
    contains_sensitive,
    is_safe_navigation_metadata,
)

FORBIDDEN_DURABLE_SUFFIXES = {".html", ".png", ".jpg", ".jpeg", ".webp"}
ARTIFACT_PATH_KEYS = {
    "artifact_path",
    "raw_json",
    "screen_index_path",
    "routes_graph_path",
    "state_flow_graph_path",
    "network_evidence_path",
}


def audit_tree(root: Path) -> dict[str, Any]:
    root = root.resolve()
    violations: list[dict[str, str]] = []
    json_files = 0
    files = 0

    scan_roots = _scan_roots(root)
    for path in sorted(
        item for scan_root in scan_roots for item in scan_root.rglob("*") if item.is_file()
    ):
        files += 1
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()

        if suffix in FORBIDDEN_DURABLE_SUFFIXES:
            violations.append(
                {
                    "file": relative,
                    "location": "$",
                    "reason": "forbidden_raw_rendered_artifact",
                }
            )
            continue

        if suffix != ".json":
            continue

        json_files += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            violations.append(
                {
                    "file": relative,
                    "location": "$",
                    "reason": "invalid_json",
                }
            )
            continue

        _audit_value(payload, relative, "$", "", violations)

    return {
        "root": root.as_posix(),
        "files": files,
        "json_files": json_files,
        "violations": violations,
        "violation_count": len(violations),
        "status": "passed" if not violations else "failed",
    }


def _scan_roots(root: Path) -> list[Path]:
    """Limit one run audit to pre-canonical crawler persistence boundaries."""
    candidates = [
        root / "raw",
        root / "processed" / "structural",
        root / "review" / "structural",
    ]
    existing = [item for item in candidates if item.exists()]
    return existing or [root]


def _audit_value(
    value: Any,
    filename: str,
    location: str,
    key: str,
    violations: list[dict[str, str]],
) -> None:
    if isinstance(value, dict):
        for raw_name, item in value.items():
            name = str(raw_name)
            lowered = name.casefold()
            child_location = f"{location}.{name}"
            if lowered in PERSISTED_DROP_KEYS:
                _add(violations, filename, child_location, "forbidden_rendered_text_key")
                continue
            if lowered in PERSISTED_FORBIDDEN_KEYS:
                _add(violations, filename, child_location, "forbidden_sensitive_key")
                continue
            if lowered in {"html", "screenshot"}:
                _add(violations, filename, child_location, "forbidden_rendered_artifact_reference")
                continue
            _audit_value(item, filename, child_location, lowered, violations)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _audit_value(item, filename, f"{location}[{index}]", key, violations)
        return

    if not isinstance(value, str) or not value:
        return

    if (
        _is_technical_persistence_key(key)
        or _is_safe_technical_token(key, value)
        or key in ARTIFACT_PATH_KEYS
    ):
        return

    if key in PERSISTED_SELECTOR_KEYS:
        metadata_key = key if key != "selector" else "navigation_origin"
        if contains_sensitive(value) and not is_safe_navigation_metadata(
            metadata_key,
            value,
        ):
            _add(violations, filename, location, "sensitive_selector")
        return

    if key in PERSISTED_ROUTE_KEYS:
        parsed = urlsplit(value)
        if parsed.scheme or parsed.netloc:
            _add(violations, filename, location, "absolute_origin_persisted")
        for _, query_value in parse_qsl(parsed.query, keep_blank_values=True):
            if query_value != "*":
                _add(violations, filename, location, "query_value_persisted")
                break
        if contains_sensitive(parsed.path):
            _add(violations, filename, location, "sensitive_route_segment")
        if parsed.fragment and not PERSISTED_INTERNAL_STATE_FRAGMENT.fullmatch(parsed.fragment):
            _add(violations, filename, location, "unsafe_route_fragment")
        return

    if contains_sensitive(value):
        _add(violations, filename, location, "sensitive_string")


def _add(
    violations: list[dict[str, str]],
    filename: str,
    location: str,
    reason: str,
) -> None:
    violations.append(
        {
            "file": filename,
            "location": location,
            "reason": reason,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit persisted crawler artifacts without printing sensitive values."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default="data/runs/pipeline",
        help="Artifact tree or one pipeline run root.",
    )
    parser.add_argument("--output", help="Optional JSON report path.")
    args = parser.parse_args()

    report = audit_tree(Path(args.root))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")

    summary = {
        "status": report["status"],
        "root": report["root"],
        "files": report["files"],
        "json_files": report["json_files"],
        "violation_count": report["violation_count"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if report["violations"]:
        print("violations:")
        for item in report["violations"][:50]:
            print(f"- {item['file']} :: {item['location']} :: {item['reason']}")
        if len(report["violations"]) > 50:
            print(f"- ... {len(report['violations']) - 50} more")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
