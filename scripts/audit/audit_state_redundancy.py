#!/usr/bin/env python3
"""
Auditoría de redundancia del modelo de estados de Chat-CBMM.

Mide cuántos UIState del registro persistido son indistinguibles entre sí
usando únicamente la evidencia durable (el `summary` que sobrevive a la
frontera de privacidad).

Un estado redundante es un síntoma de que `structural_fingerprint` se calculó
sobre información que después no se conserva: la identidad no es reproducible
ni explicable desde los artefactos.

Uso:
    python audit_state_redundancy.py data/runs/pipeline/<job_id>
    python audit_state_redundancy.py <run_a> <run_b>      # comparar dos corridas
    python audit_state_redundancy.py <run> --json          # salida machine-readable

Criterio de aceptación para State Model v2:
    redundant_states == 0
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any


def find_state_registry(run_dir: Path) -> Path:
    """Localiza state_registry.json dentro de un directorio de run."""
    direct = run_dir / "processed" / "structural" / "state_registry.json"
    if direct.exists():
        return direct
    if run_dir.name == "state_registry.json":
        return run_dir
    matches = sorted(run_dir.rglob("state_registry.json"))
    matches = [m for m in matches if not m.name.endswith(".partial.json")]
    if not matches:
        raise FileNotFoundError(f"No se encontró state_registry.json bajo {run_dir}")
    return matches[0]


def summary_key(state: dict[str, Any]) -> str:
    """Identidad observable de un estado según la evidencia durable."""
    return json.dumps(state.get("summary", {}), sort_keys=True, ensure_ascii=False)


def last_event(state: dict[str, Any]) -> tuple[str | None, str | None]:
    """Devuelve (event_type, source_state_id) del último paso del path."""
    steps = (state.get("path") or {}).get("steps") or []
    if not steps:
        return None, None
    step = steps[-1]
    return step.get("event", {}).get("event_type"), step.get("source_state_id")


def analyze(registry_path: Path) -> dict[str, Any]:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    states = payload["states"] if isinstance(payload, dict) else payload

    keys = {s["state_id"]: summary_key(s) for s in states}
    groups: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for state in states:
        groups[summary_key(state)].append(state)

    duplicate_groups = [g for g in groups.values() if len(g) > 1]
    redundant = sum(len(g) - 1 for g in duplicate_groups)

    by_event: collections.Counter[str] = collections.Counter()
    first_order = 0
    second_order = 0
    unexplained: list[dict[str, str]] = []

    for group in duplicate_groups:
        event_type, source_id = last_event(group[1])
        by_event[event_type or "sin_path"] += len(group) - 1

        if event_type == "change_pagination" or event_type is None:
            first_order += 1
            continue

        _, sibling_source = last_event(group[0])
        parents_equivalent = (
            source_id
            and sibling_source
            and source_id != sibling_source
            and keys.get(source_id) == keys.get(sibling_source)
        )
        if parents_equivalent:
            second_order += 1
        else:
            unexplained.append(
                {
                    "route": group[0].get("route", ""),
                    "event_type": event_type or "",
                    "state_ids": ", ".join(s["state_id"][-12:] for s in group),
                }
            )

    return {
        "registry": str(registry_path),
        "total_states": len(states),
        "distinct_summaries": len(groups),
        "duplicate_groups": len(duplicate_groups),
        "redundant_states": redundant,
        "redundancy_pct": round(redundant * 100 / len(states), 1) if states else 0.0,
        "redundant_by_event": dict(by_event.most_common()),
        "first_order_groups": first_order,
        "second_order_groups": second_order,
        "unexplained": unexplained,
        "passes_v2_criterion": redundant == 0,
    }


def render(report: dict[str, Any], label: str = "") -> None:
    header = f" {label} " if label else " "
    print("=" * 66)
    print(f"AUDITORÍA DE REDUNDANCIA DE ESTADOS{header}".rstrip())
    print("=" * 66)
    print(f"  registro                 {report['registry']}")
    print(f"  estados totales          {report['total_states']}")
    print(f"  summaries distintos      {report['distinct_summaries']}")
    print(f"  grupos duplicados        {report['duplicate_groups']}")
    print(f"  ESTADOS REDUNDANTES      {report['redundant_states']}  ({report['redundancy_pct']}%)")

    if report["redundant_by_event"]:
        print("\n  Evento que originó cada estado redundante:")
        for event_type, count in report["redundant_by_event"].items():
            print(f"      {event_type:22s} {count}")

    print(f"\n  grupos de 1er orden (paginación)          {report['first_order_groups']}")
    print(f"  grupos de 2do orden (padre duplicado)     {report['second_order_groups']}")

    if report["unexplained"]:
        print(f"\n  ⚠ NO EXPLICADOS ({len(report['unexplained'])}):")
        for row in report["unexplained"]:
            print(f"      {row['route']}  via {row['event_type']}  [{row['state_ids']}]")
        print("      Revisar: puede indicar sub-discriminación del summary durable.")

    verdict = "PASA" if report["passes_v2_criterion"] else "NO PASA"
    print(f"\n  CRITERIO State Model v2 (redundantes == 0): {verdict}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path, help="Directorios de run a auditar")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args()

    reports = []
    for run in args.runs:
        try:
            reports.append(analyze(find_state_registry(run)))
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            print(f"ERROR leyendo {run}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0], indent=2, ensure_ascii=False))
    else:
        for run, report in zip(args.runs, reports, strict=True):
            render(report, label=f"— {run.name[:20]}")

        if len(reports) == 2:
            before, after = reports
            delta = after["redundant_states"] - before["redundant_states"]
            print("=" * 66)
            print("COMPARACIÓN")
            print("=" * 66)
            print(
                f"  redundantes: {before['redundant_states']} → "
                f"{after['redundant_states']}  ({delta:+d})"
            )
            print(f"  estados:     {before['total_states']} → {after['total_states']}")
            print()

    return 0 if all(r["passes_v2_criterion"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
