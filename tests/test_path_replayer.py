from playwright.sync_api import sync_playwright

from src.crawler.path_replayer import PathReplayer
from src.crawler.state_registry import StateRegistry
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
      document.getElementById('submenu').style.display='block';
    ">Abrir menú</button>
    <div id="submenu" style="display:none">
      <span>Facturas</span>
    </div>
  </body>
</html>
"""


def build_profile() -> dict:
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
        },
    }


class FakeNavigator:
    def __init__(self, page):
        self.page = page

    def goto_path(self, path: str) -> None:
        assert path == ROUTE
        self.page.set_content(HTML)


class FixedRouteExtractor:
    def __init__(self, page, profile):
        self.delegate = ScreenExtractor(page, profile)

    def extract(self):
        data = self.delegate.extract()
        data["path"] = ROUTE
        data["url"] = f"http://example.invalid{ROUTE}"
        return data


def build_states(page, profile):
    navigator = FakeNavigator(page)
    navigator.goto_path(ROUTE)
    extractor = FixedRouteExtractor(page, profile)
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
        event_type=UIEventType.EXPAND_MENU,
        label="Abrir menú",
        selector="button.open-menu",
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )
    target_path = root_path.append(
        CrawlPathStep(
            source_state_id=root.state_id,
            event=event,
            target_state_id=target_id,
        )
    )
    target = registry.register_signature(
        target_signature,
        path=target_path,
    ).state
    return navigator, extractor, builder, registry, root, target


def test_path_replayer_reproduces_registered_dynamic_state():
    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        navigator, extractor, builder, registry, root, target = build_states(
            page, profile
        )

        replayer = PathReplayer(
            page=page,
            profile=profile,
            navigator=navigator,
            extractor=extractor,
            signature_builder=builder,
            registry=registry,
        )
        result = replayer.replay(
            target.path,
            expected_target_state_id=target.state_id,
        )
        browser.close()

    assert result.success is True
    assert result.completed_steps == 1
    assert result.reached_state_id == target.state_id
    assert "Facturas" in result.screen_data["visible_text"]


def test_path_replayer_refuses_non_allowed_event():
    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        navigator, extractor, builder, registry, root, target = build_states(
            page, profile
        )

        blocked = UIEvent(
            event_type=UIEventType.MUTATIVE_ACTION,
            label="Guardar",
            selector="button.open-menu",
            decision=EventDecision.DENY,
            risk_level=RiskLevel.HIGH,
        )
        path = root.path.append(
            CrawlPathStep(root.state_id, blocked, target.state_id)
        )

        replayer = PathReplayer(
            page=page,
            profile=profile,
            navigator=navigator,
            extractor=extractor,
            signature_builder=builder,
            registry=registry,
        )
        result = replayer.replay(path)
        browser.close()

    assert result.success is False
    assert "no autorizado" in result.error
