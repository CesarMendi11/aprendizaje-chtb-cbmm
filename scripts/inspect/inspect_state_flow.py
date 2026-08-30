from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resume el state-flow graph generado por el crawler."
    )
    parser.add_argument(
        "--graph",
        default="data/processed/structural/state_flow_graph.json",
        help="Ruta al archivo state_flow_graph.json.",
    )
    return parser.parse_args()


def load_graph(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"No existe {path}. Ejecuta primero el crawler estructural."
        )
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("El state-flow graph debe ser un objeto JSON.")
    return payload


def main() -> None:
    args = parse_args()
    path = Path(args.graph)
    graph = load_graph(path)

    states = graph.get("states", [])
    transitions = graph.get("transitions", [])
    roots = [
        state
        for state in states
        if (state.get("path") or {}).get("depth", 0) == 0
    ]
    dynamic = [
        state
        for state in states
        if (state.get("path") or {}).get("depth", 0) > 0
    ]
    categories = Counter(
        (transition.get("event") or {}).get("event_type", "unknown")
        for transition in transitions
    )
    restoration = Counter(
        (transition.get("metadata") or {}).get(
            "restore_strategy", "not_recorded"
        )
        for transition in transitions
    )
    depths = Counter(
        (state.get("path") or {}).get("depth", 0)
        for state in states
    )
    changed_routes = sum(
        1 for transition in transitions if transition.get("changed_route")
    )

    summary_path = path.with_name("state_exploration_summary.json")
    exploration_summary = {}
    if summary_path.exists():
        exploration_summary = load_graph(summary_path)

    print("State-flow graph inspeccionado correctamente.")
    print(f"Archivo: {path}")
    print(f"Estados totales: {len(states)}")
    print(f"Estados raíz por ruta: {len(roots)}")
    print(f"Estados dinámicos: {len(dynamic)}")
    print(f"Transiciones observadas: {len(transitions)}")
    print(f"Profundidades: {dict(sorted(depths.items()))}")
    print(f"Categorías: {dict(sorted(categories.items()))}")
    print(f"Restauración: {dict(sorted(restoration.items()))}")
    print(f"Transiciones con cambio de ruta: {changed_routes}")
    if exploration_summary:
        print(
            "Profundidad máxima configurada: "
            f"{exploration_summary.get('max_event_depth')}"
        )
        print(
            "Estados fuente explorados/pendientes: "
            f"{exploration_summary.get('frontier_explored_count', 0)}/"
            f"{exploration_summary.get('frontier_pending_count', 0)}"
        )


if __name__ == "__main__":
    main()
