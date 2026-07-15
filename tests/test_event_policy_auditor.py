from src.review.event_policy_auditor import build_event_policy_audit


def build_profile() -> dict:
    return {
        "erp": {"code": "test", "base_url": "http://localhost:8080"},
        "navigation": {"home_url": "/admin/home"},
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": [],
        },
        "safety": {
            "default_decision": "deny",
            "allowed_event_categories": [
                "expand_menu",
                "submit_search",
                "navigation_link",
            ],
            "review_event_categories": ["unknown", "expand_row"],
            "forbidden_event_categories": ["mutative_action"],
            "dangerous_keywords": ["Guardar"],
            "safe_keywords": ["Buscar"],
        },
        "ui_events": {
            "enabled": True,
            "skip_link_navigation": True,
            "min_candidate_score": 3,
            "candidate_limits": {
                "max_candidates_per_screen": 30,
                "max_events_per_state": 5,
                "max_text_length": 100,
            },
            "exploration_budget": {
                "exclude_global_navigation_outside_home": True,
                "category_limits": {
                    "submit_search": 1,
                    "expand_menu": 0,
                },
                "home_category_limits": {"expand_menu": 10},
            },
        },
        "forms": {"enabled": False, "allow_submit": False},
    }


def test_event_policy_audit_reports_pipeline_and_exclusions():
    screen_index = {
        "screens": [
            {
                "path": "/admin/facturas",
                "functional_title": "Facturas",
                "links": [],
                "buttons": [
                    {
                        "text": "Buscar",
                        "tag": "button",
                        "selector": "main button.search",
                        "region": "main_content",
                    },
                    {
                        "text": "Abrir menú",
                        "tag": "button",
                        "selector": "aside button.open-menu",
                        "region": "global_navigation",
                    },
                ],
                "custom_interactives": [],
            }
        ]
    }

    audit = build_event_policy_audit(build_profile(), screen_index)

    assert audit["screens_count"] == 1
    assert audit["pipeline_totals"]["raw_candidates_count"] == 2
    assert audit["pipeline_totals"]["selected_for_exploration_count"] == 1
    assert audit["selection_exclusion_totals"][
        "global_navigation_outside_home"
    ] == 1
