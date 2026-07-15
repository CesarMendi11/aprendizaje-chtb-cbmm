from scripts.inspect_event_plan import build_plan


def profile():
    return {
        "erp": {
            "name": "ERP Test",
            "code": "test",
            "base_url": "http://example.invalid",
        },
        "navigation": {"home_url": "/admin/home"},
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": [],
        },
        "safety": {
            "default_decision": "deny",
            "allowed_event_categories": [
                "expand_menu",
                "open_readonly_view",
            ],
            "review_event_categories": ["unknown"],
            "forbidden_event_categories": ["mutative_action"],
            "dangerous_keywords": ["Guardar"],
            "safe_keywords": ["Abrir", "Ver"],
        },
        "ui_events": {
            "enabled": True,
            "min_candidate_score": 3,
            "skip_link_navigation": True,
            "max_event_depth": 1,
            "home_navigation_enabled": True,
            "explore_local_route_roots": True,
            "home_event_categories": ["expand_menu"],
            "local_event_categories": ["open_readonly_view"],
            "candidate_limits": {
                "max_candidates_per_screen": 20,
                "max_events_per_state": 10,
                "max_text_length": 100,
            },
            "exploration_budget": {
                "category_limits": {"open_readonly_view": 2},
                "home_category_limits": {"expand_menu": 2},
            },
        },
        "forms": {"enabled": False, "allow_submit": False},
    }


def test_build_plan_applies_home_and_local_category_scopes():
    screen_index = {
        "screens": [
            {
                "path": "/admin/home",
                "title": "Dashboard",
                "links": [],
                "buttons": [
                    {
                        "text": "Abrir módulo",
                        "selector": "button.module",
                        "tag": "button",
                        "aria_expanded": "false",
                        "region": "global_navigation",
                    },
                    {
                        "text": "Ver ayuda",
                        "selector": "button.help",
                        "tag": "button",
                        "region": "main_content",
                    },
                ],
                "custom_interactives": [],
            },
            {
                "path": "/admin/detail",
                "title": "Detalle",
                "links": [],
                "buttons": [
                    {
                        "text": "Abrir módulo",
                        "selector": "button.module",
                        "tag": "button",
                        "aria_expanded": "false",
                        "region": "global_navigation",
                    },
                    {
                        "text": "Ver detalles",
                        "selector": "button.details",
                        "tag": "button",
                        "region": "main_content",
                    },
                ],
                "custom_interactives": [],
            },
        ]
    }

    plan = build_plan(profile(), screen_index)

    assert plan["selected_events_count"] == 2
    assert plan["categories"] == {
        "expand_menu": 1,
        "open_readonly_view": 1,
    }
    assert plan["screens"][0]["events"][0]["event_category"] == "expand_menu"
    assert (
        plan["screens"][1]["events"][0]["event_category"]
        == "open_readonly_view"
    )
