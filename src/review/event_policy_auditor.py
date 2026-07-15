from __future__ import annotations

from collections import Counter
from typing import Any

from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.policy.route_policy import RoutePolicy


def build_event_policy_audit(
    profile: dict[str, Any],
    screen_index: dict[str, Any],
) -> dict[str, Any]:
    """Construye una auditoría reproducible sobre el índice de la misma ejecución."""

    discovery = EventCandidateDiscovery(profile, RoutePolicy(profile))
    decision_totals: Counter[str] = Counter()
    category_totals: Counter[str] = Counter()
    region_totals: Counter[str] = Counter()
    pipeline_totals: Counter[str] = Counter()
    selection_exclusion_totals: Counter[str] = Counter()
    screens: list[dict[str, Any]] = []

    for screen in screen_index.get("screens", []):
        candidates = discovery._discover_all_candidates(screen)
        pipeline = discovery.build_pipeline_report(screen)
        decisions = Counter(candidate.decision for candidate in candidates)
        categories = Counter(candidate.event_category for candidate in candidates)
        regions = Counter(
            str(candidate.metadata.get("region") or "unknown")
            for candidate in candidates
        )

        decision_totals.update(decisions)
        category_totals.update(categories)
        region_totals.update(regions)
        pipeline_totals.update(
            {
                key: value
                for key, value in pipeline.items()
                if key.endswith("_count") and isinstance(value, int)
            }
        )
        selection_exclusion_totals.update(
            pipeline.get("selection_exclusions", {})
        )

        screens.append(
            {
                "route": screen.get("route") or screen.get("path"),
                "title": screen.get("functional_title") or screen.get("title"),
                "title_source": screen.get("title_source"),
                "candidates_count": len(candidates),
                "pipeline": pipeline,
                "decisions": dict(sorted(decisions.items())),
                "categories": dict(sorted(categories.items())),
                "regions": dict(sorted(regions.items())),
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
        "region_totals": dict(sorted(region_totals.items())),
        "pipeline_totals": dict(sorted(pipeline_totals.items())),
        "selection_exclusion_totals": dict(
            sorted(selection_exclusion_totals.items())
        ),
        "screens": screens,
    }
