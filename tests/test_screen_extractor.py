from playwright.sync_api import sync_playwright

from src.extraction.screen_extractor import ScreenExtractor


TEST_URL = "http://localhost:8080/admin/home"


def build_profile() -> dict:
    return {
        "extraction": {
            "max_visible_text_chars": 8000,
        }
    }


def load_fake_page(page, html: str, url: str = TEST_URL) -> None:
    """
    Simula una página real sin levantar servidor local.

    Así evitamos errores como:
    net::ERR_CONNECTION_REFUSED
    """

    page.route(
        url,
        lambda route: route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=html.encode("utf-8"),
        ),
    )

    page.goto(url)


def test_screen_extractor_extracts_basic_screen_data():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Test</title>
      </head>
      <body>
        <h1>Panel principal</h1>

        <nav class="sidebar-menu">
          <a href="/admin/home">Inicio</a>
          <a href="/admin/facturas">Facturas</a>
        </nav>

        <button>Buscar</button>
        <button>Ver detalle</button>

        <label for="customer">Cliente</label>
        <input id="customer" name="customer" type="text" placeholder="Buscar cliente" />

        <table>
          <thead>
            <tr>
              <th>Número</th>
              <th>Fecha</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>001</td>
              <td>2026-01-01</td>
              <td>Pendiente</td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        extractor = ScreenExtractor(page, build_profile())
        data = extractor.extract()

        browser.close()

    assert data["title"] == "ERP Test"
    assert data["path"] == "/admin/home"
    assert "Panel principal" in data["visible_text"]

    assert len(data["links"]) == 2
    assert data["links"][0]["text"] == "Inicio"
    assert data["links"][1]["href"] == "/admin/facturas"

    assert len(data["buttons"]) == 2
    assert data["buttons"][0]["text"] == "Buscar"

    assert len(data["inputs"]) == 1
    assert data["inputs"][0]["name"] == "customer"
    assert data["inputs"][0]["label"] == "Cliente"

    assert len(data["tables"]) == 1
    assert data["tables"][0]["headers"] == ["Número", "Fecha", "Estado"]


def test_screen_extractor_detects_custom_interactives():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <title>ERP Menu</title>
      </head>
      <body>
        <fuse-vertical-navigation-collapsable-item aria-expanded="false">
          <span>Cuentas por cobrar</span>
        </fuse-vertical-navigation-collapsable-item>

        <div class="sidebar-menu" onclick="openMenu()">
          Inventario
        </div>
      </body>
    </html>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        extractor = ScreenExtractor(page, build_profile())
        data = extractor.extract()

        browser.close()

    texts = [item["text"] for item in data["custom_interactives"]]

    assert "Cuentas por cobrar" in texts
    assert "Inventario" in texts


def test_screen_extractor_truncates_visible_text():
    long_text = "A" * 200

    html = f"""
    <!DOCTYPE html>
    <html>
      <head>
        <title>Long Text</title>
      </head>
      <body>
        <p>{long_text}</p>
      </body>
    </html>
    """

    profile = {
        "extraction": {
            "max_visible_text_chars": 50,
        }
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        load_fake_page(page, html)

        extractor = ScreenExtractor(page, profile)
        data = extractor.extract()

        browser.close()

    assert len(data["visible_text"]) == 50
    assert data["visible_text_truncated"] is True