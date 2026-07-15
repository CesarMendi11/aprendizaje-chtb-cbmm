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
            "Audita la clasificación y seguridad de eventos usando un "
            "screen_index existente, sin abrir el ERP."
        )
    )
    parser.add_argument("--profile", default="configs/cbmm.yaml")
    parser.add_argument(
        "--screen-index",
        default="data/processed/structural/screen_index.json",
    )
    parser.add_argument(
        "--output",
        default="data/processed/structural/event_policy_audit.json",
    )
    return parser.parse_args()


def build_audit(
    profile: dict[str, Any],
    screen_index: dict[str, Any],
) -> dict[str, Any]:
    discovery = EventCandidateDiscovery(profile, RoutePolicy(profile))

    decision_totals: Counter[str] = Counter()
    category_totals: Counter[str] = Counter()
    screens: list[dict[str, Any]] = []

    for screen in screen_index.get("screens", []):
        candidates = discovery._discover_all_candidates(screen)
        decisions = Counter(candidate.decision for candidate in candidates)
        categories = Counter(candidate.event_category for candidate in candidates)

        decision_totals.update(decisions)
        category_totals.update(categories)

        screens.append(
            {
                "route": screen.get("route") or screen.get("path"),
                "title": screen.get("title"),
                "candidates_count": len(candidates),
                "decisions": dict(sorted(decisions.items())),
                "categories": dict(sorted(categories.items())),
                "denied": [
                    candidate.to_dict()
                    for candidate in candidates
                    if candidate.decision == "deny"
                ],
                "review": [
                    candidate.to_dict()
                    for candidate in candidates
                    if candidate.decision == "review"
                ],
            }
        )

    return {
        "profile": profile.get("erp", {}).get("code"),
        "screens_count": len(screens),
        "decision_totals": dict(sorted(decision_totals.items())),
        "category_totals": dict(sorted(category_totals.items())),
        "screens": screens,
    }


def main() -> int:
    args = parse_args()
    profile = ProfileLoader(args.profile).load()

    screen_index_path = Path(args.screen_index)
    if not screen_index_path.exists():
        raise FileNotFoundError(f"No existe screen_index: {screen_index_path}")

    screen_index = json.loads(screen_index_path.read_text(encoding="utf-8"))
    audit = build_audit(profile, screen_index)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Auditoría de política de eventos completada.")
    print(f"Pantallas: {audit['screens_count']}")
    print(f"Decisiones: {audit['decision_totals']}")
    print(f"Categorías: {audit['category_totals']}")
    print(f"Resultado: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
