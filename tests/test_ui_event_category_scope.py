from playwright.sync_api import sync_playwright

from src.crawler.state_signature import StateSignatureBuilder
from src.crawler.ui_event_explorer import UIEventExplorer
from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.extraction.screen_extractor import ScreenExtractor
from src.policy.route_policy import RoutePolicy


def build_profile():
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
            "dangerous_keywords": ["Guardar", "Eliminar"],
            "safe_keywords": ["Abrir", "Ver"],
        },
        "state_detection": {
            "visible_text_limit": 4000,
            "ignore_query_values": True,
            "ignore_table_row_count": True,
        },
        "extraction": {"max_visible_text_chars": 8000},
        "ui_events": {
            "enabled": True,
            "min_candidate_score": 3,
            "event_wait_ms": 0,
            "click_timeout_ms": 1000,
            "skip_link_navigation": True,
            "restore_after_exploration": False,
            "capture_event_artifacts": False,
            "candidate_limits": {
                "max_candidates_per_screen": 20,
                "max_events_per_state": 10,
                "max_text_length": 100,
            },
            "exploration_budget": {
                "category_limits": {
                    "expand_menu": 2,
                    "open_readonly_view": 2,
                },
                "home_category_limits": {
                    "expand_menu": 2,
                    "open_readonly_view": 2,
                },
            },
        },
        "browser_interaction": {
            "click_timeout_ms": 1000,
            "click_attempts": 1,
            "retry_wait_ms": 0,
            "pre_click_wait_ms": 0,
            "scroll_into_view": True,
        },
        "forms": {"enabled": False, "allow_submit": False},
    }


def test_ui_event_explorer_respects_allowed_category_scope():
    html = """
    <html><head><title>Test</title></head><body>
      <button class="menu" aria-expanded="false" onclick="
        document.getElementById('menu-panel').style.display='block';
      ">Abrir menú</button>
      <div id="menu-panel" style="display:none">Menú abierto</div>

      <button class="details" onclick="
        document.getElementById('details-panel').style.display='block';
      ">Ver detalles</button>
      <div id="details-panel" style="display:none">Detalles visibles</div>
    </body></html>
    """
    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        policy = RoutePolicy(profile)
        extractor = ScreenExtractor(page, profile)
        explorer = UIEventExplorer(
            page=page,
            profile=profile,
            extractor=extractor,
            candidate_discovery=EventCandidateDiscovery(profile, policy),
            state_signature_builder=StateSignatureBuilder.from_profile(profile),
        )

        results = explorer.explore_current_state(
            allowed_categories={"open_readonly_view"},
        )
        browser.close()

    assert len(results) == 1
    assert results[0].event.event_type.value == "open_readonly_view"
    assert results[0].candidate["label"] == "Ver detalles"
    assert results[0].changed is True
