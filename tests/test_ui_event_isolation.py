from playwright.sync_api import sync_playwright

from src.crawler.path_replayer import PathReplayer
from src.crawler.state_registry import StateRegistry
from src.crawler.state_restorer import StateRestorer
from src.crawler.state_signature import StateSignatureBuilder
from src.crawler.ui_event_explorer import UIEventExplorer
from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.extraction.screen_extractor import ScreenExtractor
from src.models.crawl_path import CrawlPath
from src.policy.route_policy import RoutePolicy


ROUTE = "/admin/home"
HTML = """
<!DOCTYPE html>
<html>
  <head><title>ERP Test</title></head>
  <body>
    <h1>Dashboard</h1>
    <button class="menu-a" onclick="
      document.getElementById('panel-a').style.display='block';
    ">Abrir menú A</button>
    <button class="menu-b" onclick="
      document.getElementById('panel-b').style.display='block';
    ">Abrir menú B</button>
    <div id="panel-a" style="display:none">Contenido A</div>
    <div id="panel-b" style="display:none">Contenido B</div>
  </body>
</html>
"""


def profile() -> dict:
    return {
        "erp": {"base_url": "http://example.invalid"},
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": [],
            "page_wait_ms": 0,
        },
        "safety": {
            "default_decision": "deny",
            "allowed_event_categories": ["expand_menu"],
            "review_event_categories": ["unknown"],
            "forbidden_event_categories": ["mutative_action"],
            "dangerous_keywords": ["Guardar", "Eliminar"],
            "safe_keywords": ["Abrir"],
        },
        "extraction": {"max_visible_text_chars": 8000},
        "ui_events": {
            "enabled": True,
            "min_candidate_score": 3,
            "event_wait_ms": 0,
            "click_timeout_ms": 1000,
            "skip_link_navigation": True,
            "restore_after_exploration": True,
            "capture_event_artifacts": False,
            "candidate_limits": {
                "max_candidates_per_screen": 20,
                "max_events_per_state": 10,
                "max_text_length": 100,
            },
        },
        "state_replay": {
            "page_wait_ms": 0,
            "step_wait_ms": 0,
            "click_timeout_ms": 1000,
            "verify_each_step": True,
            "restore_attempts": 1,
        },
        "forms": {"enabled": False, "allow_submit": False},
    }


class FakeNavigator:
    def __init__(self, page):
        self.page = page

    def goto_path(self, path: str) -> None:
        assert path == ROUTE
        self.page.set_content(HTML)


class FixedRouteExtractor:
    def __init__(self, page, cfg):
        self.delegate = ScreenExtractor(page, cfg)

    def extract(self):
        data = self.delegate.extract()
        data["path"] = ROUTE
        data["url"] = f"http://example.invalid{ROUTE}"
        return data


def test_ui_event_explorer_restores_source_before_each_candidate():
    cfg = profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        navigator = FakeNavigator(page)
        navigator.goto_path(ROUTE)
        extractor = FixedRouteExtractor(page, cfg)
        builder = StateSignatureBuilder()
        registry = StateRegistry()

        source_signature = builder.build(extractor.extract())
        source_id = registry.build_state_id(
            source_signature.structural_fingerprint
        )
        source_path = CrawlPath(root_state_id=source_id)
        source_state = registry.register_signature(
            source_signature,
            path=source_path,
        ).state

        replayer = PathReplayer(
            page=page,
            profile=cfg,
            navigator=navigator,
            extractor=extractor,
            signature_builder=builder,
            registry=registry,
        )
        restorer = StateRestorer(
            profile=cfg,
            navigator=navigator,
            extractor=extractor,
            signature_builder=builder,
            registry=registry,
            path_replayer=replayer,
        )
        discovery = EventCandidateDiscovery(cfg, RoutePolicy(cfg))
        explorer = UIEventExplorer(
            page=page,
            profile=cfg,
            extractor=extractor,
            candidate_discovery=discovery,
            state_signature_builder=builder,
            state_restorer=restorer,
        )

        results = explorer.explore_current_state(
            screen_data=extractor.extract(),
            source_state=source_state,
        )
        final_text = page.locator("body").inner_text()
        browser.close()

    changed = [result for result in results if result.changed]
    assert len(changed) == 2
    assert all(result.restored_before for result in changed)
    assert all(result.source_state_id == source_state.state_id for result in changed)

    by_label = {result.event.label: result for result in changed}
    assert "Contenido A" in by_label["Abrir menú A"].after_screen_data["visible_text"]
    assert "Contenido B" not in by_label["Abrir menú A"].after_screen_data["visible_text"]
    assert "Contenido B" in by_label["Abrir menú B"].after_screen_data["visible_text"]
    assert "Contenido A" not in by_label["Abrir menú B"].after_screen_data["visible_text"]

    # El explorador deja el navegador nuevamente en el estado fuente.
    assert "Contenido A" not in final_text
    assert "Contenido B" not in final_text
