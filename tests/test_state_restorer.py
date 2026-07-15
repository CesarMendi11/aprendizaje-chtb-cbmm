from playwright.sync_api import sync_playwright

from src.crawler.path_replayer import PathReplayer
from src.crawler.state_registry import StateRegistry
from src.crawler.state_restorer import StateRestorer
from src.crawler.state_signature import StateSignatureBuilder
from src.extraction.screen_extractor import ScreenExtractor
from src.models.crawl_path import CrawlPath, CrawlPathStep
from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType


ROUTE = "/admin/home"
HTML = """
<!DOCTYPE html>
<html>
  <head><title>ERP Test</title></head>
  <body>
    <h1>Dashboard</h1>
    <button class="open-menu" onclick="
      document.getElementById('panel').style.display='block';
    ">Abrir panel</button>
    <div id="panel" style="display:none">Panel abierto</div>
  </body>
</html>
"""


def profile() -> dict:
    return {
        "erp": {"base_url": "http://example.invalid"},
        "exploration": {"page_wait_ms": 0},
        "extraction": {"max_visible_text_chars": 8000},
        "ui_events": {"event_wait_ms": 0, "click_timeout_ms": 1000},
        "state_replay": {
            "page_wait_ms": 0,
            "step_wait_ms": 0,
            "click_timeout_ms": 1000,
            "verify_each_step": True,
            "restore_attempts": 1,
        },
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


def setup_components(page):
    cfg = profile()
    navigator = FakeNavigator(page)
    navigator.goto_path(ROUTE)
    extractor = FixedRouteExtractor(page, cfg)
    builder = StateSignatureBuilder()
    registry = StateRegistry()

    root_signature = builder.build(extractor.extract())
    root_id = registry.build_state_id(root_signature.structural_fingerprint)
    root_path = CrawlPath(root_state_id=root_id)
    root = registry.register_signature(root_signature, path=root_path).state

    page.locator("button.open-menu").click()
    target_signature = builder.build(extractor.extract())
    target_id = registry.build_state_id(target_signature.structural_fingerprint)
    event = UIEvent(
        event_type=UIEventType.OPEN_READONLY_VIEW,
        label="Abrir panel",
        selector="button.open-menu",
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )
    target_path = root_path.append(
        CrawlPathStep(root.state_id, event, target_id)
    )
    target = registry.register_signature(
        target_signature,
        path=target_path,
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
    return root, target, restorer


def test_state_restorer_restores_root_by_direct_navigation():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        root, target, restorer = setup_components(page)

        assert page.locator("#panel").is_visible()
        result = restorer.restore(root)
        panel_visible = page.locator("#panel").is_visible()
        browser.close()

    assert result.success is True
    assert result.strategy == "direct_route"
    assert panel_visible is False


def test_state_restorer_replays_path_for_dynamic_state():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        root, target, restorer = setup_components(page)

        restorer.restore(root)
        result = restorer.restore(target)
        panel_visible = page.locator("#panel").is_visible()
        browser.close()

    assert result.success is True
    assert result.strategy == "path_replay"
    assert panel_visible is True
