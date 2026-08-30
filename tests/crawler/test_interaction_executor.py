from playwright.sync_api import sync_playwright

from src.browser.interaction_executor import BrowserInteractionExecutor


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
