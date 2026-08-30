from __future__ import annotations

import pytest

from src.crawler.module_scope import ModuleCrawlBoundary, ModuleCrawlBoundaryError


def scope_payload():
    return {
        "root_module_id": "module:tracking",
        "module_ids": [
            "module:tracking",
            "module:integrations",
        ],
        "known_screen_routes": [
            "/sales/tracking/",
            "/sales/tracking?tab=summary",
            "/sales/tracking/integrations/external",
        ],
        "navigation_path": ["Sales", "Tracking"],
        "navigation_origin_path": ["#sales", "#tracking"],
    }


def test_boundary_builds_deterministic_entry_steps_and_normalizes_known_routes():
    boundary = ModuleCrawlBoundary.from_payload(scope_payload())

    assert boundary.root_module_id == "module:tracking"
    assert boundary.module_ids == (
        "module:tracking",
        "module:integrations",
    )
    assert boundary.known_screen_routes == (
        "/sales/tracking",
        "/sales/tracking/integrations/external",
    )
    assert [(step.depth, step.label, step.selector) for step in boundary.entry_steps] == [
        (1, "Sales", "#sales"),
        (2, "Tracking", "#tracking"),
    ]


def test_known_subtree_routes_are_allowed_without_dynamic_provenance():
    boundary = ModuleCrawlBoundary.from_payload(scope_payload())

    assert boundary.is_known_route("/sales/tracking?tab=detail")
    assert boundary.allows_discovered_route("/sales/tracking/")
    assert boundary.allows_discovered_route(
        "/sales/tracking/integrations/external?view=1"
    )
    assert not boundary.is_known_route("/sales/orders")


def test_unknown_route_requires_expand_menu_reveal_inside_selected_branch():
    boundary = ModuleCrawlBoundary.from_payload(scope_payload())
    candidate = "/sales/tracking/integrations/new-screen"

    assert boundary.allows_discovered_route(
        candidate,
        menu_selectors=("#sales", "#tracking", "#integrations"),
        event_category="expand_menu",
        newly_revealed=True,
    )

    assert not boundary.allows_discovered_route(
        candidate,
        menu_selectors=("#sales", "#tracking", "#integrations"),
        event_category="expand_menu",
        newly_revealed=False,
    )
    assert not boundary.allows_discovered_route(
        candidate,
        menu_selectors=("#sales", "#tracking", "#integrations"),
        event_category="open_readonly_view",
        newly_revealed=True,
    )


def test_sibling_branch_cannot_expand_unknown_route_scope():
    boundary = ModuleCrawlBoundary.from_payload(scope_payload())

    assert boundary.is_inside_selected_branch(
        ("#sales", "#tracking", "#integrations")
    )
    assert not boundary.is_inside_selected_branch(("#sales", "#orders"))
    assert not boundary.allows_discovered_route(
        "/sales/orders/new-screen",
        menu_selectors=("#sales", "#orders"),
        event_category="expand_menu",
        newly_revealed=True,
    )


def test_arbitrary_unknown_links_do_not_widen_module_scope():
    boundary = ModuleCrawlBoundary.from_payload(scope_payload())

    assert not boundary.allows_discovered_route(
        "/sales/tracking/report",
        menu_selectors=("#sales", "#tracking"),
    )
    assert not boundary.allows_discovered_route(
        "/sales/tracking/report",
        menu_selectors=("#sales", "#tracking"),
        event_category="navigation_link",
        newly_revealed=True,
    )


def test_boundary_rejects_incomplete_or_inconsistent_pinned_scope():
    with pytest.raises(ModuleCrawlBoundaryError, match="module_scope debe ser"):
        ModuleCrawlBoundary.from_payload(None)

    broken_root = scope_payload()
    broken_root["root_module_id"] = "tracking"
    with pytest.raises(ModuleCrawlBoundaryError, match="identificador canónico"):
        ModuleCrawlBoundary.from_payload(broken_root)

    missing_root = scope_payload()
    missing_root["module_ids"] = ["module:integrations"]
    with pytest.raises(ModuleCrawlBoundaryError, match="no contiene"):
        ModuleCrawlBoundary.from_payload(missing_root)

    mismatched_path = scope_payload()
    mismatched_path["navigation_origin_path"] = ["#sales"]
    with pytest.raises(ModuleCrawlBoundaryError, match="misma profundidad"):
        ModuleCrawlBoundary.from_payload(mismatched_path)

    missing_path = scope_payload()
    missing_path["navigation_origin_path"] = []
    with pytest.raises(ModuleCrawlBoundaryError, match="reproducibles"):
        ModuleCrawlBoundary.from_payload(missing_path)
