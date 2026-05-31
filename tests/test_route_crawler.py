import json

from playwright.sync_api import sync_playwright

from src.crawler.route_crawler import RouteCrawler


BASE_URL = "http://localhost:8080"


def build_profile(tmp_path):
    return {
        "erp": {
            "name": "ERP Test",
            "code": "erp_test",
            "base_url": BASE_URL,
        },
        "login": {
            "url": "/login",
            "username_selector": "input[name='username']",
            "password_selector": "input[name='password']",
            "submit_role_name": "Ingresar",
            "success_url_contains": "/admin/home",
        },
        "navigation": {
            "home_url": "/admin/home",
            "home_wait_ms": 0,
        },
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": ["/admin/configuracion"],
            "start_modules": [],
            "max_depth": 3,
            "max_pages_total": 10,
            "page_wait_ms": 0,
        },
        "safety": {
            "dangerous_keywords": ["Eliminar", "Guardar", "Aprobar"],
            "safe_keywords": ["Ver", "Buscar", "Consultar"],
        },
        "extraction": {
            "max_visible_text_chars": 8000,
        },
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


def register_fake_erp_routes(page):
    pages = {
        f"{BASE_URL}/admin/home": """
        <!DOCTYPE html>
        <html>
          <head><title>Home</title></head>
          <body>
            <h1>Panel principal</h1>
            <a href="/admin/facturas">Facturas</a>
            <a href="/admin/clientes">Clientes</a>
            <a href="/admin/configuracion/usuarios">Usuarios bloqueado</a>
          </body>
        </html>
        """,
        f"{BASE_URL}/admin/facturas": """
        <!DOCTYPE html>
        <html>
          <head><title>Facturas</title></head>
          <body>
            <h1>Facturas</h1>
            <a href="/admin/home">Inicio</a>
            <button>Buscar</button>
            <button>Eliminar factura</button>
          </body>
        </html>
        """,
        f"{BASE_URL}/admin/clientes": """
        <!DOCTYPE html>
        <html>
          <head><title>Clientes</title></head>
          <body>
            <h1>Clientes</h1>
            <a href="/admin/home">Inicio</a>
            <button>Ver detalle</button>
          </body>
        </html>
        """,
    }

    def handler(route):
        url = route.request.url
        html = pages.get(
            url,
            """
            <!DOCTYPE html>
            <html>
              <head><title>404</title></head>
              <body>No encontrado</body>
            </html>
            """,
        )

        route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html.encode("utf-8"),
        )

    page.route(f"{BASE_URL}/**", handler)


def test_route_crawler_discovers_allowed_routes_and_saves_outputs(tmp_path):
    profile = build_profile(tmp_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        register_fake_erp_routes(page)

        crawler = RouteCrawler(page, profile)
        summary = crawler.crawl()

        browser.close()

    assert summary.visited_count == 3
    assert summary.nodes_count == 3
    assert summary.edges_count >= 2

    routes_graph_path = tmp_path / "data/processed/structural/routes_graph.json"
    screen_index_path = tmp_path / "data/processed/structural/screen_index.json"

    assert routes_graph_path.exists()
    assert screen_index_path.exists()

    with routes_graph_path.open("r", encoding="utf-8") as file:
        graph = json.load(file)

    routes = {node["route"] for node in graph["nodes"]}

    assert "/admin/home" in routes
    assert "/admin/facturas" in routes
    assert "/admin/clientes" in routes
    assert "/admin/configuracion/usuarios" not in routes

    with screen_index_path.open("r", encoding="utf-8") as file:
        index = json.load(file)

    indexed_routes = {screen["route"] for screen in index["screens"]}

    assert indexed_routes == {
        "/admin/home",
        "/admin/facturas",
        "/admin/clientes",
    }


def test_route_crawler_creates_uncertainty_for_dangerous_actions(tmp_path):
    profile = build_profile(tmp_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        register_fake_erp_routes(page)

        crawler = RouteCrawler(page, profile)
        crawler.crawl()

        browser.close()

    review_dir = tmp_path / "data/review/structural"
    uncertainty_files = list(review_dir.glob("*_uncertainty.json"))

    assert uncertainty_files

    content = "\n".join(
        path.read_text(encoding="utf-8") for path in uncertainty_files
    )

    assert "acciones peligrosas" in content or "Eliminar factura" in content

def test_route_crawler_uses_ui_events_to_discover_hidden_links(tmp_path):
    profile = build_profile(tmp_path)
    profile["ui_events"] = {
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
    }
    profile["forms"] = {
        "enabled": False,
        "allow_submit": False,
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        def handler(route):
            url = route.request.url

            if url == f"{BASE_URL}/admin/home":
                html = """
                <!DOCTYPE html>
                <html>
                  <head><title>Home</title></head>
                  <body>
                    <h1>Panel principal</h1>

                    <button class="open-menu" onclick="
                      document.getElementById('submenu').style.display='block';
                    ">
                      Abrir menú
                    </button>

                    <div id="submenu" style="display:none">
                      <a href="/admin/facturas">Facturas</a>
                      <a href="/admin/clientes">Clientes</a>
                    </div>
                  </body>
                </html>
                """
            elif url == f"{BASE_URL}/admin/facturas":
                html = """
                <!DOCTYPE html>
                <html>
                  <head><title>Facturas</title></head>
                  <body>
                    <h1>Facturas</h1>
                  </body>
                </html>
                """
            elif url == f"{BASE_URL}/admin/clientes":
                html = """
                <!DOCTYPE html>
                <html>
                  <head><title>Clientes</title></head>
                  <body>
                    <h1>Clientes</h1>
                  </body>
                </html>
                """
            else:
                html = """
                <!DOCTYPE html>
                <html>
                  <head><title>404</title></head>
                  <body>No encontrado</body>
                </html>
                """

            route.fulfill(
                status=200,
                content_type="text/html; charset=utf-8",
                body=html.encode("utf-8"),
            )

        page.route(f"{BASE_URL}/**", handler)

        crawler = RouteCrawler(page, profile)
        summary = crawler.crawl()

        browser.close()

    assert summary.visited_count == 3
    assert summary.nodes_count >= 4
    assert summary.edges_count >= 3

    routes_graph_path = tmp_path / "data/processed/structural/routes_graph.json"

    with routes_graph_path.open("r", encoding="utf-8") as file:
        graph = json.load(file)

    routes = {node["route"] for node in graph["nodes"]}

    assert "/admin/home" in routes
    assert "/admin/facturas" in routes
    assert "/admin/clientes" in routes

    ui_state_nodes = [
        node for node in graph["nodes"]
        if "#state:" in node["route"]
    ]

    assert ui_state_nodes