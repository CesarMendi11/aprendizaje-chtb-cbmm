import json

from playwright.sync_api import sync_playwright

from src.crawler.route_crawler import RouteCrawler
from src.extraction.screen_extractor import ScreenExtractor


HOME = "/admin/home"
DETAIL = "/admin/detail"


def build_profile(tmp_path, max_event_depth: int):
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
            "max_depth": 3,
            "max_pages_total": 10,
            "page_wait_ms": 0,
        },
        "safety": {
            "default_decision": "deny",
            "allowed_event_categories": [
                "navigation_link",
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
            "max_event_depth": max_event_depth,
            "home_navigation_enabled": True,
            "explore_local_route_roots": True,
            "recursive_state_exploration": True,
            "home_event_categories": ["expand_menu"],
            "local_event_categories": ["open_readonly_view"],
            "restore_after_exploration": True,
            "capture_event_artifacts": False,
            "candidate_limits": {
                "max_candidates_per_screen": 20,
                "max_events_per_state": 10,
                "max_text_length": 100,
            },
            "exploration_budget": {
                "exclude_global_navigation_outside_home": True,
                "category_limits": {"open_readonly_view": 3},
                "home_category_limits": {"expand_menu": 3},
            },
        },
        "state_replay": {
            "page_wait_ms": 0,
            "step_wait_ms": 0,
            "click_timeout_ms": 1000,
            "verify_each_step": True,
            "restore_attempts": 1,
        },
        "browser_interaction": {
            "click_timeout_ms": 1000,
            "click_attempts": 1,
            "retry_wait_ms": 0,
            "pre_click_wait_ms": 0,
            "scroll_into_view": True,
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
          <button class="module" aria-expanded="false" onclick="
            this.setAttribute('aria-expanded', 'true');
            document.getElementById('submenu').style.display='block';
          ">Abrir módulo</button>
          <div id="submenu" style="display:none">
            <a href="/admin/detail">Detalle</a>
          </div>
        </body></html>
    """,
    DETAIL: """
        <html><head><title>Detalle</title></head><body>
          <main>
            <h1>Detalle</h1>
            <button class="show-details" onclick="
              document.getElementById('details').style.display='block';
            ">Ver detalles</button>
            <section id="details" style="display:none">
              <p>Detalle visible</p>
              <button class="show-extra" onclick="
                document.getElementById('extra').style.display='block';
              ">Ver información adicional</button>
            </section>
            <div id="extra" style="display:none">Información adicional visible</div>
          </main>
        </body></html>
    """,
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

    def extract(self, title_hint: str = ""):
        data = self.delegate.extract(title_hint=title_hint)
        data["path"] = self.navigator.current_path()
        data["url"] = f"http://example.invalid{data['path']}"
        return data


def run_crawler(tmp_path, max_event_depth: int):
    cfg = build_profile(tmp_path, max_event_depth)

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

    graph_path = tmp_path / "data/processed/structural/state_flow_graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    return summary, graph


def transition_categories(graph):
    return [item["event"]["event_type"] for item in graph["transitions"]]


def state_depths(graph):
    return sorted((item.get("path") or {}).get("depth", 0) for item in graph["states"])


def test_depth_zero_preserves_home_navigation_without_local_events(tmp_path):
    summary, graph = run_crawler(tmp_path, max_event_depth=0)

    assert summary.visited_count == 2
    assert graph["summary"] == {"states_count": 3, "transitions_count": 1}
    assert transition_categories(graph) == ["expand_menu"]
    assert state_depths(graph) == [0, 0, 1]


def test_depth_one_explores_local_events_from_route_roots(tmp_path):
    summary, graph = run_crawler(tmp_path, max_event_depth=1)

    assert summary.visited_count == 2
    assert graph["summary"] == {"states_count": 4, "transitions_count": 2}
    assert sorted(transition_categories(graph)) == ["expand_menu", "open_readonly_view"]
    assert state_depths(graph) == [0, 0, 1, 1]
    assert summary.state_frontier_pending_count == 0


def test_depth_two_replays_dynamic_state_and_discovers_second_level(tmp_path):
    summary, graph = run_crawler(tmp_path, max_event_depth=2)

    assert summary.visited_count == 2
    assert graph["summary"] == {"states_count": 5, "transitions_count": 3}
    assert transition_categories(graph).count("open_readonly_view") == 2
    assert state_depths(graph) == [0, 0, 1, 1, 2]
    assert summary.state_frontier_pending_count == 0
    assert summary.state_frontier_explored_count == 4
