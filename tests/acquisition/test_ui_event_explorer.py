from playwright.sync_api import sync_playwright

from erp_assistant.acquisition.crawling.state_signature import StateSignatureBuilder
from erp_assistant.acquisition.crawling.ui_event_explorer import UIEventExplorer
from erp_assistant.acquisition.discovery.event_candidate_discovery import EventCandidateDiscovery
from erp_assistant.acquisition.extraction.screen_extractor import ScreenExtractor
from erp_assistant.acquisition.models.crawl_path import CrawlPath, CrawlPathStep
from erp_assistant.acquisition.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from erp_assistant.acquisition.models.ui_state import UIState
from erp_assistant.acquisition.policy.route_policy import RoutePolicy


BASE_URL = "http://localhost:8080"
TEST_URL = f"{BASE_URL}/admin/home"


def build_profile() -> dict:
    return {
        "erp": {
            "base_url": BASE_URL,
        },
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": [],
        },
        "safety": {
            "dangerous_keywords": [
                "Guardar",
                "Eliminar",
                "Aprobar",
                "Emitir",
                "Enviar",
            ],
            "safe_keywords": [
                "Ver",
                "Buscar",
                "Consultar",
                "Detalle",
                "Cerrar",
                "Cancelar",
                "Abrir",
            ],
        },
        "extraction": {
            "max_visible_text_chars": 8000,
        },
        "ui_events": {
            "enabled": True,
            "min_candidate_score": 3,
            "event_wait_ms": 100,
            "click_timeout_ms": 1000,
            "skip_link_navigation": True,
            "candidate_limits": {
                "max_candidates_per_screen": 80,
                "max_events_per_state": 20,
                "max_text_length": 100,
            },
        },
        "forms": {
            "enabled": False,
            "allow_submit": False,
        },
    }


def load_fake_page(page, html: str, url: str = TEST_URL) -> None:
    page.route(
        url,
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html.encode("utf-8"),
        ),
    )

    page.goto(url)


def build_explorer(page, profile: dict) -> UIEventExplorer:
    policy = RoutePolicy(profile)
    extractor = ScreenExtractor(page, profile)
    candidate_discovery = EventCandidateDiscovery(profile, policy)
    signature_builder = StateSignatureBuilder()

    return UIEventExplorer(
        page=page,
        profile=profile,
        extractor=extractor,
        candidate_discovery=candidate_discovery,
        state_signature_builder=signature_builder,
    )


def test_ui_event_explorer_detects_state_change_after_safe_click():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Test</title>
      </head>
      <body>
        <h1>Dashboard</h1>

        <button class="open-menu" onclick="
          document.getElementById('submenu').style.display='block';
        ">
          Abrir menú
        </button>

        <div id="submenu" style="display:none">
          <a href="/admin/facturas">Facturas</a>
          <a href="/admin/retenciones">Retenciones</a>
        </div>
      </body>
    </html>
    """

    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        explorer = build_explorer(page, profile)
        results = explorer.explore_current_state()

        browser.close()

    changed_results = [result for result in results if result.changed]

    assert changed_results
    assert changed_results[0].before_route == "/admin/home"
    assert changed_results[0].after_route == "/admin/home"

    after_links = changed_results[0].after_screen_data["links"]
    hrefs = {link["href"] for link in after_links}

    assert "/admin/facturas" in hrefs
    assert "/admin/retenciones" in hrefs


def test_ui_event_explorer_does_not_click_dangerous_button():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Test</title>
      </head>
      <body>
        <h1>Facturas</h1>

        <button class="delete" onclick="
          document.body.insertAdjacentHTML('beforeend', '<p id=deleted>Eliminado</p>');
        ">
          Eliminar factura
        </button>
      </body>
    </html>
    """

    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        explorer = build_explorer(page, profile)
        results = explorer.explore_current_state()

        deleted_exists = page.locator("#deleted").count()

        browser.close()

    assert results == []
    assert deleted_exists == 0


def test_ui_event_explorer_skips_link_navigation_candidates():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Test</title>
      </head>
      <body>
        <h1>Dashboard</h1>
        <a href="/admin/facturas">Facturas</a>
      </body>
    </html>
    """

    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        explorer = build_explorer(page, profile)
        results = explorer.explore_current_state()

        browser.close()

    assert results == []


def test_ui_event_explorer_returns_error_result_for_invalid_selector():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Test</title>
      </head>
      <body>
        <h1>Dashboard</h1>
        <button class="open-menu">Abrir menú</button>
      </body>
    </html>
    """

    profile = build_profile()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        explorer = build_explorer(page, profile)

        screen_data = {
            "path": "/admin/home",
            "title": "ERP Test",
            "visible_text": "Dashboard Abrir menú",
            "links": [],
            "buttons": [
                {
                    "text": "Abrir menú",
                    "tag": "button",
                    "type": None,
                    "role": None,
                    "selector": "button.does-not-exist",
                }
            ],
            "inputs": [],
            "tables": [],
            "custom_interactives": [],
        }

        results = explorer.explore_current_state(screen_data)

        browser.close()

    assert len(results) == 1
    assert results[0].changed is False
    assert results[0].error is not None

def test_ui_event_explorer_reports_interaction_attempts_after_animation():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Test</title>
        <style>
          @keyframes moving { from { transform: translateX(0); }
                              to { transform: translateX(80px); } }
          .open-menu { animation: moving 900ms linear; }
        </style>
      </head>
      <body>
        <h1>Dashboard</h1>
        <button class="open-menu" onclick="
          document.getElementById('submenu').style.display='block';
        ">Abrir menú</button>
        <div id="submenu" style="display:none">Facturas</div>
      </body>
    </html>
    """

    profile = build_profile()
    profile["browser_interaction"] = {
        "click_timeout_ms": 350,
        "click_attempts": 4,
        "retry_wait_ms": 250,
        "pre_click_wait_ms": 0,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        load_fake_page(page, html)
        explorer = build_explorer(page, profile)
        results = explorer.explore_current_state()
        browser.close()

    changed = [result for result in results if result.changed]
    assert changed
    assert changed[0].interaction_attempts >= 2
    assert changed[0].interaction_strategy == "validated_click"


def test_ui_event_filter_skips_menu_selector_already_used_in_state_path():
    explorer = UIEventExplorer.__new__(UIEventExplorer)
    explorer.skip_link_navigation = True

    prior_event = UIEvent(
        event_type=UIEventType.EXPAND_MENU,
        label="General",
        selector="nav > collapsable-item:nth-of-type(2)",
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )
    path = CrawlPath(root_state_id="root").append(
        CrawlPathStep(source_state_id="root", event=prior_event, target_state_id="s1")
    )
    source_state = UIState(
        state_id="s1",
        route="/admin/home",
        title="Dashboard",
        exact_signature="exact",
        structural_signature="struct",
        summary={},
        path=path,
    )

    repeated = EventCandidateDiscovery(build_profile(), RoutePolicy(build_profile()))._from_custom_interactives([
        {
            "text": "General Año Personas",
            "tag": "fuse-vertical-navigation-collapsable-item",
            "selector": "nav > collapsable-item:nth-of-type(2)",
            "region": "global_navigation",
        }
    ])[0]
    repeated.event_category = "expand_menu"
    repeated.decision = "allow"

    fresh = EventCandidateDiscovery(build_profile(), RoutePolicy(build_profile()))._from_custom_interactives([
        {
            "text": "Solicitudes",
            "tag": "fuse-vertical-navigation-collapsable-item",
            "selector": "nav > collapsable-item:nth-of-type(2) > collapsable-item:nth-of-type(1)",
            "region": "global_navigation",
        }
    ])[0]
    fresh.event_category = "expand_menu"
    fresh.decision = "allow"

    filtered = explorer._filter_candidates_for_ui_events(
        [repeated, fresh],
        allowed_categories={"expand_menu"},
        source_state=source_state,
    )

    assert [candidate.label for candidate in filtered] == ["Solicitudes"]


def test_ui_event_filter_keeps_only_descendant_menu_on_recursive_branch():
    explorer = UIEventExplorer.__new__(UIEventExplorer)
    explorer.skip_link_navigation = True

    prior_event = UIEvent(
        event_type=UIEventType.EXPAND_MENU,
        label="Transporte",
        selector=(
            "app-root > layout > admin-layout > navigation > "
            "collapsable-item:nth-of-type(11)"
        ),
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )
    path = CrawlPath(root_state_id="root").append(
        CrawlPathStep(source_state_id="root", event=prior_event, target_state_id="s1")
    )
    source_state = UIState(
        state_id="s1",
        route="/admin/home",
        title="Dashboard",
        exact_signature="exact",
        structural_signature="struct",
        summary={},
        path=path,
    )

    sibling = EventCandidateDiscovery(build_profile(), RoutePolicy(build_profile()))._from_custom_interactives([
        {
            "text": "General",
            "tag": "fuse-vertical-navigation-collapsable-item",
            "selector": (
                "app-root > layout > admin-layout > navigation > "
                "collapsable-item:nth-of-type(2)"
            ),
            "region": "global_navigation",
        }
    ])[0]
    sibling.event_category = "expand_menu"
    sibling.decision = "allow"

    nested = EventCandidateDiscovery(build_profile(), RoutePolicy(build_profile()))._from_custom_interactives([
        {
            "text": "Solicitudes",
            "tag": "fuse-vertical-navigation-collapsable-item",
            "selector": (
                "admin-layout > navigation > collapsable-item:nth-of-type(11) > "
                "div:nth-of-type(2) > collapsable-item"
            ),
            "region": "global_navigation",
        }
    ])[0]
    nested.event_category = "expand_menu"
    nested.decision = "allow"

    filtered = explorer._filter_candidates_for_ui_events(
        [sibling, nested],
        allowed_categories={"expand_menu"},
        source_state=source_state,
    )

    assert [candidate.label for candidate in filtered] == ["Solicitudes"]


def test_selector_descendant_accepts_shortened_equivalent_prefix():
    ancestor = (
        "app-root > layout > admin-layout > navigation > "
        "collapsable-item:nth-of-type(11)"
    )
    candidate = (
        "admin-layout > navigation > collapsable-item:nth-of-type(11) > "
        "div > collapsable-item"
    )

    assert UIEventExplorer._selector_is_descendant(candidate, ancestor) is True
    assert UIEventExplorer._selector_is_descendant(
        "app-root > layout > admin-layout > navigation > collapsable-item:nth-of-type(2)",
        ancestor,
    ) is False
