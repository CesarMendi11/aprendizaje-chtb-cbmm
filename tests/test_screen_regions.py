from playwright.sync_api import sync_playwright

from src.extraction.screen_extractor import ScreenExtractor


def test_screen_extractor_separates_layout_regions_and_resolves_title():
    html = """
    <html>
      <head><title>Dashboard</title></head>
      <body>
        <aside class="sidebar"><a href="/admin/home">Inicio</a></aside>
        <header><button>Usuario</button></header>
        <main>
          <h1>Consulta de facturas</h1>
          <button>Buscar</button>
          <table><thead><tr><th>Número</th></tr></thead></table>
        </main>
      </body>
    </html>
    """
    profile = {
        "extraction": {
            "max_visible_text_chars": 8000,
            "regions": {
                "global_navigation": ["aside"],
                "header": ["header"],
                "main_content": ["main"],
                "footer": ["footer"],
                "dialog": ["[role='dialog']"],
                "volatile": [],
            },
            "title_resolution": {
                "generic_document_titles": ["Dashboard"],
            },
        }
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        data = ScreenExtractor(page, profile).extract(title_hint="Facturas")
        browser.close()

    assert data["document_title"] == "Dashboard"
    assert data["functional_title"] == "Consulta de facturas"
    assert data["title_source"] == "main_heading"
    assert data["links"][0]["region"] == "global_navigation"
    assert data["buttons"][0]["region"] == "header"
    assert data["buttons"][1]["region"] == "main_content"
    assert data["regions"]["main_content"]["visible_text"].startswith(
        "Consulta de facturas"
    )
    assert len(data["global_links"]) == 1
    assert data["local_links"] == []
