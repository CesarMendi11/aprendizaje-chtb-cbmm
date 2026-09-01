#!/usr/bin/env python3
"""
Verifica el modelo de estados en un run del crawler.

Comprueba tres cosas que State Model v2 debe cumplir a la vez:

  1. No hay estados redundantes (identidad reproducible desde evidencia durable).
  2. No hay fallos de restauración de estado.
  3. Los eventos de contenido SE CONSERVARON como self-loops.

El punto 3 es el que evita el falso éxito: colapsar estados es fácil si además
se pierde el conocimiento de que la acción existe. Aquí se comprueba que
"Siguiente página" sigue registrada aunque ya no cree una pantalla nueva.

Uso:
    python scripts/audit/verify_state_model.py data/runs/pipeline/<job_id>
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

CONTENT_EVENT_HINTS = ("change_pagination", "sort", "filter")


def load(run_dir: Path, name: str) -> Any:
    direct = run_dir / "processed" / "structural" / name
    if direct.exists():
        return json.loads(direct.read_text(encoding="utf-8"))
    matches = [m for m in run_dir.rglob(name) if not m.name.endswith(".partial.json")]
    if not matches:
        raise FileNotFoundError(f"No se encontró {name} bajo {run_dir}")
    return json.loads(matches[0].read_text(encoding="utf-8"))


def check_redundancy(run_dir: Path) -> tuple[bool, list[str]]:
    states = load(run_dir, "state_registry.json")["states"]
    groups: dict[str, list] = collections.defaultdict(list)
    for state in states:
        key = json.dumps(state.get("summary", {}), sort_keys=True, ensure_ascii=False)
        groups[key].append(state)

    redundant = sum(len(g) - 1 for g in groups.values() if len(g) > 1)
    lines = [
        f"estados totales        {len(states)}",
        f"summaries distintos    {len(groups)}",
        f"estados redundantes    {redundant}",
    ]
    for group in groups.values():
        if len(group) > 1:
            lines.append(f"   ! {group[0].get('route')} x{len(group)}")
    return redundant == 0, lines


def check_restore_failures(run_dir: Path) -> tuple[bool, list[str]]:
    review = run_dir / "review" / "structural"
    if not review.exists():
        return True, ["sin carpeta de review (nada que revisar)"]

    failures = []
    for path in sorted(review.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        reason = str(payload.get("reason") or "")
        if "restore_failed" in reason:
            failures.append(f"   ! {payload.get('route')} :: {reason}")

    lines = [f"fallos de restore      {len(failures)}"] + failures
    return not failures, lines


def check_self_loops(run_dir: Path) -> tuple[bool, list[str]]:
    graph = load(run_dir, "state_flow_graph.json")
    transitions = graph.get("transitions") or []
    if isinstance(transitions, int):
        return False, ["state_flow_graph.json no expone la lista de transiciones"]

    self_loops = [t for t in transitions if t.get("source_state_id") == t.get("target_state_id")]
    by_effect = collections.Counter(
        (t.get("metadata") or {}).get("effect", "sin_effect") for t in transitions
    )
    by_event = collections.Counter(
        (t.get("event") or {}).get("event_type", "?") for t in self_loops
    )

    content_events = [
        t
        for t in transitions
        if any(
            hint in str((t.get("event") or {}).get("event_type", ""))
            for hint in CONTENT_EVENT_HINTS
        )
    ]
    preserved = [t for t in content_events if t.get("source_state_id") == t.get("target_state_id")]

    lines = [
        f"transiciones totales   {len(transitions)}",
        f"self-loops             {len(self_loops)}",
        f"eventos de contenido   {len(content_events)}  (self-loop: {len(preserved)})",
        f"por effect             {dict(by_effect)}",
    ]
    if by_event:
        lines.append(f"self-loops por evento  {dict(by_event)}")

    lost = [
        f"   ! {t.get('source_state_id', '')[-12:]} "
        f"{(t.get('event') or {}).get('event_type')} NO es self-loop"
        for t in content_events
        if t.get("source_state_id") != t.get("target_state_id")
    ]
    lines.extend(lost)

    # Un evento de contenido que no quedó como self-loop significa que todavía
    # está creando un estado; cero eventos de contenido significa que se
    # perdieron por completo.
    ok = bool(content_events) and not lost
    if not content_events:
        lines.append(
            "   ! No se registró ningún evento de contenido: "
            "el conocimiento de la acción se perdió."
        )
    return ok, lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()

    checks = (
        ("1. Identidad reproducible", check_redundancy),
        ("2. Restauración de estado", check_restore_failures),
        ("3. Eventos de contenido conservados", check_self_loops),
    )

    print("=" * 66)
    print(f"VERIFICACIÓN STATE MODEL V2 — {args.run.name[:24]}")
    print("=" * 66)

    results = []
    for title, check in checks:
        try:
            ok, lines = check(args.run)
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            ok, lines = False, [f"ERROR: {exc}"]
        results.append(ok)
        print(f"\n{'PASA' if ok else 'FALLA'}  {title}")
        for line in lines:
            print(f"      {line}")

    verdict = all(results)
    print("\n" + "=" * 66)
    print("VEREDICTO:", "PASA" if verdict else "REVISAR")
    print("=" * 66)
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
