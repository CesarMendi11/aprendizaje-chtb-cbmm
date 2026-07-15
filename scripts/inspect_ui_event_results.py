from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from src.config.profile_loader import ProfileLoader


TIMESTAMP_RE = re.compile(r"_(\d{8}_\d{6})_uncertainty\.json$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resume la ejecución real de eventos UI usando el archivo más "
            "reciente de cada pantalla."
        )
    )
    parser.add_argument(
        "--profile",
        default="configs/cbmm.yaml",
        help="Ruta del perfil YAML.",
    )
    parser.add_argument(
        "--review-dir",
        default=None,
        help=(
            "Directorio de resultados estructurales. Por defecto usa "
            "output.review_structural_dir del perfil."
        ),
    )
    parser.add_argument(
        "--output",
        default="data/processed/structural/ui_event_execution_audit.json",
        help="Archivo JSON de salida.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} debe contener un objeto JSON.")
    return payload


def file_timestamp(path: Path) -> str:
    match = TIMESTAMP_RE.search(path.name)
    if match:
        return match.group(1)
    return ""


def latest_result_files(review_dir: Path) -> list[Path]:
    by_route: dict[str, tuple[str, Path]] = {}
    for path in review_dir.glob("*_ui_events_*_uncertainty.json"):
        try:
            payload = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        route = str(payload.get("route") or "")
        if not route:
            continue
        timestamp = file_timestamp(path)
        current = by_route.get(route)
        if current is None or timestamp > current[0]:
            by_route[route] = (timestamp, path)
    return [item[1] for item in sorted(by_route.values(), key=lambda item: item[1].name)]


def infer_outcome(result: dict[str, Any]) -> str:
    explicit = result.get("outcome")
    if explicit:
        return str(explicit)
    error = result.get("error")
    if error == "state_restore_failed":
        return "restore_failed"
    if error and not result.get("interaction_succeeded"):
        return "interaction_failed"
    if error:
        return "execution_error"
    if result.get("changed"):
        return "changed"
    return "unchanged"


def build_audit(files: list[Path]) -> dict[str, Any]:
    outcomes = Counter()
    categories = Counter()
    changed_categories = Counter()
    restore_strategies = Counter()
    errors = Counter()
    interactions = Counter()
    routes = []

    for path in files:
        payload = load_json(path)
        route_results = []
        for result in payload.get("results", []):
            candidate = result.get("candidate") or {}
            category = str(candidate.get("event_category") or "unknown")
            outcome = infer_outcome(result)
            categories[category] += 1
            outcomes[outcome] += 1
            if result.get("changed"):
                changed_categories[category] += 1
            restore_strategies[str(result.get("restore_strategy") or "none")] += 1
            if result.get("error"):
                errors[str(result.get("error"))] += 1
            if "interaction_succeeded" in result:
                interaction_status = (
                    "succeeded"
                    if result.get("interaction_succeeded")
                    else "not_succeeded"
                )
            elif outcome in {"changed", "unchanged"}:
                interaction_status = "inferred_succeeded"
            else:
                interaction_status = "unknown"
            interactions[interaction_status] += 1

            route_results.append(
                {
                    "label": candidate.get("label"),
                    "category": category,
                    "outcome": outcome,
                    "changed": bool(result.get("changed")),
                    "error": result.get("error"),
                    "restore_strategy": result.get("restore_strategy"),
                    "interaction_attempts": result.get("interaction_attempts", 0),
                    "interaction_strategy": result.get("interaction_strategy"),
                    "interaction_succeeded": bool(
                        result.get("interaction_succeeded")
                    ),
                    "restore_diagnostics": result.get(
                        "restore_diagnostics", {}
                    ),
                    "after_observation": result.get("after_observation", {}),
                    "target_state_id": result.get("target_state_id"),
                }
            )

        routes.append(
            {
                "route": payload.get("route"),
                "source_state_id": payload.get("source_state_id"),
                "file": str(path),
                "results_count": len(route_results),
                "results": route_results,
            }
        )

    return {
        "audit_type": "ui_event_execution_audit",
        "files_count": len(files),
        "routes_count": len(routes),
        "events_count": sum(categories.values()),
        "outcomes": dict(sorted(outcomes.items())),
        "categories": dict(sorted(categories.items())),
        "changed_categories": dict(sorted(changed_categories.items())),
        "restore_strategies": dict(sorted(restore_strategies.items())),
        "interactions": dict(sorted(interactions.items())),
        "errors": dict(sorted(errors.items())),
        "routes": routes,
    }


def main() -> None:
    args = parse_args()
    profile = ProfileLoader(args.profile).load()
    review_dir = Path(
        args.review_dir
        or profile.get("output", {}).get(
            "review_structural_dir", "data/review/structural"
        )
    )
    if not review_dir.exists():
        raise FileNotFoundError(f"No existe {review_dir}.")

    files = latest_result_files(review_dir)
    if not files:
        raise FileNotFoundError(
            "No se encontraron resultados *_ui_events_*_uncertainty.json."
        )

    audit = build_audit(files)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Auditoría de ejecución UI completada.")
    print(f"Archivos recientes: {audit['files_count']}")
    print(f"Rutas con eventos: {audit['routes_count']}")
    print(f"Eventos evaluados: {audit['events_count']}")
    print(f"Resultados: {audit['outcomes']}")
    print(f"Categorías: {audit['categories']}")
    print(f"Cambios por categoría: {audit['changed_categories']}")
    print(f"Restauración: {audit['restore_strategies']}")
    print(f"Interacciones: {audit['interactions']}")
    print(f"Errores: {audit['errors']}")
    print(f"Resultado: {output_path}")
    print("-" * 80)
    for route in audit["routes"]:
        print(route["route"])
        for result in route["results"]:
            print(
                "  - "
                f"[{result['category']}] "
                f"{result['label'] or '<sin texto>'}: "
                f"{result['outcome']}"
            )


if __name__ == "__main__":
    main()
