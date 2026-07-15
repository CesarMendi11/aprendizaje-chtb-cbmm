import json

from playwright.sync_api import sync_playwright

from src.crawler.route_crawler import RouteCrawler
from src.extraction.screen_extractor import ScreenExtractor


HOME = "/admin/home"


def profile(tmp_path):
    return {
        "erp": {
            "name": "ERP Test",
            "code": "test",
            "base_url": "http://example.invalid",
        },
        "login": {
            "url": "/login",
            "username_selector": "#user",
            "password_selector": "#password",
            "submit_role_name": "Ingresar",
            "success_url_contains": HOME,
        },
        "navigation": {"home_url": HOME, "home_wait_ms": 0},
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": [],
            "start_modules": [],
            "max_depth": 2,
            "max_pages_total": 10,
            "page_wait_ms": 0,
        },
        "safety": {
            "default_decision": "deny",
            "allowed_event_categories": [
                "navigation_link",
                "expand_menu",
            ],
            "review_event_categories": ["unknown"],
            "forbidden_event_categories": ["mutative_action"],
            "dangerous_keywords": ["Guardar", "Eliminar"],
            "safe_keywords": ["Abrir"],
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
            "max_event_depth": 0,
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
        "output": {
            "raw_playwright_dir": str(tmp_path / "data/raw/playwright"),
            "html_dir": str(tmp_path / "data/raw/html"),
            "screenshots_dir": str(tmp_path / "data/raw/screenshots"),
            "marked_screenshots_dir": str(tmp_path / "data/raw/marked_screenshots"),
            "processed_structural_dir": str(tmp_path / "data/processed/structural"),
            "processed_semantic_dir": str(tmp_path / "data/processed/semantic"),
            "review_structural_dir": str(tmp_path / "data/review/structural"),
            "review_semantic_dir": str(tmp_path / "data/review/semantic"),
            "approved_neo4j_dir": str(tmp_path / "data/approved/neo4j"),
            "approved_chromadb_dir": str(tmp_path / "data/approved/chromadb"),
            "rejected_dir": str(tmp_path / "data/rejected"),
            "cache_dir": str(tmp_path / "data/cache"),
        },
    }


PAGES = {
    HOME: """
        <html><head><title>Home</title></head><body>
          <h1>Dashboard</h1>
          <button class="menu-a" onclick="
            document.getElementById('a').style.display='block'
          ">Abrir menú A</button>
          <button class="menu-b" onclick="
            document.getElementById('b').style.display='block'
          ">Abrir menú B</button>
          <div id="a" style="display:none"><a href="/admin/a">Pantalla A</a></div>
          <div id="b" style="display:none"><a href="/admin/b">Pantalla B</a></div>
        </body></html>
    """,
    "/admin/a": "<html><head><title>A</title></head><body><h1>A</h1></body></html>",
    "/admin/b": "<html><head><title>B</title></head><body><h1>B</h1></body></html>",
}


class FakeNavigator:
    def __init__(self, page):
        self.page = page
        self.path = HOME

    def goto_home(self):
        self.goto_path(HOME)

    def goto_path(self, path: str, wait_until: str = "domcontentloaded"):
        self.path = path
        self.page.set_content(PAGES[path])

    def current_path(self):
        return self.path

    def get_html(self):
        return self.page.content()

    def screenshot_bytes(self, full_page: bool = True):
        return self.page.screenshot(full_page=full_page)

    def click_text_if_visible(self, text, exact=False, timeout_ms=2000):
        locator = self.page.get_by_text(text, exact=exact).first
        if not locator.is_visible():
            return False
        locator.click()
        return True


class FixedRouteExtractor:
    def __init__(self, page, cfg, navigator):
        self.delegate = ScreenExtractor(page, cfg)
        self.navigator = navigator

    def extract(self):
        data = self.delegate.extract()
        data["path"] = self.navigator.current_path()
        data["url"] = f"http://example.invalid{data['path']}"
        return data


def test_route_crawler_builds_isolated_state_flow_graph(tmp_path):
    cfg = profile(tmp_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        crawler = RouteCrawler(page, cfg)

        navigator = FakeNavigator(page)
        extractor = FixedRouteExtractor(page, cfg, navigator)
        crawler.navigator = navigator
        crawler.extractor = extractor
        crawler.ui_event_explorer.extractor = extractor
        crawler.path_replayer.navigator = navigator
        crawler.path_replayer.extractor = extractor
        crawler.state_restorer.navigator = navigator
        crawler.state_restorer.extractor = extractor

        summary = crawler.crawl()
        browser.close()

    assert summary.visited_count == 3
    assert summary.states_count == 5
    assert summary.state_transitions_count == 2

    graph_path = tmp_path / "data/processed/structural/state_flow_graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert graph["summary"] == {
        "states_count": 5,
        "transitions_count": 2,
    }

    labels = {
        transition["event"]["label"]
        for transition in graph["transitions"]
    }
    assert labels == {"Abrir menú A", "Abrir menú B"}

    dynamic_states = [
        state
        for state in graph["states"]
        if state["path"] and state["path"]["depth"] == 1
    ]
    assert len(dynamic_states) == 2

    visible_texts = {
        state["summary"]["visible_text"] for state in dynamic_states
    }
    assert any("pantalla a" in text and "pantalla b" not in text for text in visible_texts)
    assert any("pantalla b" in text and "pantalla a" not in text for text in visible_texts)
