from playwright.sync_api import sync_playwright

from erp_assistant.acquisition.browser.interaction_executor import BrowserInteractionExecutor


def profile() -> dict:
    return {
        "browser_interaction": {
            "click_timeout_ms": 350,
            "click_attempts": 4,
            "retry_wait_ms": 250,
            "pre_click_wait_ms": 0,
            "scroll_into_view": True,
        }
    }


def test_interaction_executor_retries_until_animated_element_is_stable():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <style>
          @keyframes moving { from { transform: translateX(0); }
                              to { transform: translateX(80px); } }
          #menu { animation: moving 900ms linear; }
        </style>
      </head>
      <body>
        <button id="menu" onclick="document.body.dataset.clicked='yes'">
          General
        </button>
      </body>
    </html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        executor = BrowserInteractionExecutor(page, profile())
        result = executor.click("#menu")
        clicked = page.locator("body").get_attribute("data-clicked")
        browser.close()

    assert result.success is True
    assert result.attempts >= 2
    assert clicked == "yes"


def test_interaction_executor_returns_diagnostic_for_missing_selector():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<html><body></body></html>")
        executor = BrowserInteractionExecutor(page, profile())
        result = executor.click("#does-not-exist")
        browser.close()

    assert result.success is False
    assert result.attempts == 4
    assert "timeout" in (result.error or "").lower()


def test_interaction_executor_waits_for_global_blocker_to_disappear():
    html = """
    <html><body>
      <div id="busy" style="position:fixed;inset:0;z-index:10"></div>
      <button id="target" onclick="document.body.dataset.clicked='yes'">Abrir</button>
      <script>setTimeout(() => document.querySelector('#busy').remove(), 600)</script>
    </body></html>
    """
    cfg = profile()
    cfg["ui_readiness"] = {
        "enabled": True,
        "blocking_selectors": ["#busy"],
        "timeout_ms": 2000,
        "poll_interval_ms": 50,
        "clear_stability_ms": 50,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        executor = BrowserInteractionExecutor(page, cfg)
        result = executor.click("#target")
        clicked = page.locator("body").get_attribute("data-clicked")
        browser.close()

    assert result.success is True
    assert clicked == "yes"


def test_interaction_executor_fails_closed_when_global_blocker_persists():
    html = """
    <html><body>
      <div id="busy" style="position:fixed;inset:0;z-index:10"></div>
      <button id="target" onclick="document.body.dataset.clicked='yes'">Abrir</button>
    </body></html>
    """
    cfg = profile()
    cfg["ui_readiness"] = {
        "enabled": True,
        "blocking_selectors": ["#busy"],
        "timeout_ms": 200,
        "poll_interval_ms": 50,
        "clear_stability_ms": 50,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        executor = BrowserInteractionExecutor(page, cfg)
        result = executor.click("#target")
        clicked = page.locator("body").get_attribute("data-clicked")
        browser.close()

    assert result.success is False
    assert "ui_readiness_timeout" in (result.error or "")
    assert clicked is None
