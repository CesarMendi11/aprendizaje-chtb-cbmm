from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.config.profile_loader import ProfileLoader
from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.policy.route_policy import RoutePolicy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Muestra los eventos que el crawler intentaría ejecutar sin abrir "
            "ni modificar el ERP."
        )
    )
    parser.add_argument(
        "--profile",
        default="configs/cbmm.yaml",
        help="Ruta del perfil YAML.",
    )
    parser.add_argument(
        "--screen-index",
        default="data/processed/structural/screen_index.json",
        help="Ruta del screen_index.json existente.",
    )
    parser.add_argument(
        "--output",
        default="data/processed/structural/event_execution_plan.json",
        help="Archivo JSON donde guardar el plan.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}.")
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} debe contener un objeto JSON.")
    return payload


def screen_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    screens = payload.get("screens", [])
    if isinstance(screens, dict):
        return list(screens.values())
    if isinstance(screens, list):
        return screens
    return []


def allowed_categories(profile: dict[str, Any], route: str) -> set[str]:
    ui_events = profile.get("ui_events", {})
    home = profile.get("navigation", {}).get("home_url", "")
    if route == home:
        if not ui_events.get("home_navigation_enabled", True):
            return set()
        return set(ui_events.get("home_event_categories", ["expand_menu"]))

    if not ui_events.get("explore_local_route_roots", True):
        return set()
    if int(ui_events.get("max_event_depth", 0)) < 1:
        return set()
    return set(ui_events.get("local_event_categories", []))


def build_plan(
    profile: dict[str, Any],
    screen_index: dict[str, Any],
) -> dict[str, Any]:
    policy = RoutePolicy(profile)
    discovery = EventCandidateDiscovery(profile, policy)
    screens_plan = []
    categories = Counter()

    for screen in screen_entries(screen_index):
        route = str(screen.get("path") or screen.get("route") or "")
        scope = allowed_categories(profile, route)
        candidates = discovery.discover_exploration_candidates(screen)
        selected = [
            candidate
            for candidate in candidates
            if candidate.event_category in scope
            and candidate.event_category != "navigation_link"
            and not candidate.dangerous
        ]
        categories.update(candidate.event_category for candidate in selected)

        screens_plan.append(
            {
                "route": route,
                "title": (
                    screen.get("functional_title")
                    or screen.get("title")
                    or route
                ),
                "allowed_categories": sorted(scope),
                "selected_count": len(selected),
                "events": [candidate.to_dict() for candidate in selected],
            }
        )

    return {
        "plan_type": "safe_ui_event_execution_preview",
        "max_event_depth": int(
            profile.get("ui_events", {}).get("max_event_depth", 0)
        ),
        "screens_count": len(screens_plan),
        "selected_events_count": sum(
            item["selected_count"] for item in screens_plan
        ),
        "categories": dict(sorted(categories.items())),
        "screens": screens_plan,
    }


def main() -> None:
    args = parse_args()
    profile = ProfileLoader(args.profile).load()
    screen_index = load_json(Path(args.screen_index))
    plan = build_plan(profile, screen_index)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Plan de eventos inspeccionado sin abrir el ERP.")
    print(f"Pantallas: {plan['screens_count']}")
    print(f"Eventos seleccionados: {plan['selected_events_count']}")
    print(f"Categorías: {plan['categories']}")
    print(f"Resultado: {output_path}")
    print("-" * 80)
    for screen in plan["screens"]:
        if not screen["events"]:
            continue
        print(f"{screen['route']}: {screen['title']}")
        for event in screen["events"]:
            metadata = event.get("metadata") or {}
            print(
                "  - "
                f"[{event.get('event_category')}] "
                f"{event.get('label') or '<sin texto>'} "
                f"({metadata.get('region', 'main_content')})"
            )


if __name__ == "__main__":
    main()
