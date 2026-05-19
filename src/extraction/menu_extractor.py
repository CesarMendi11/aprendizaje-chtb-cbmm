import os
import json
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

ERP_BASE_URL = os.getenv("ERP_BASE_URL")
ERP_USERNAME = os.getenv("ERP_USERNAME")
ERP_PASSWORD = os.getenv("ERP_PASSWORD")

OUTPUT_DIR = Path("data/raw/playwright")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAIN_MODULES = [
    "Cuentas por cobrar",
    "General",
    "Gerencial",
    "Permisos",
    "Rentas",
    "Seguridad",
    "SRI",
    "Tramites",
]


def login(page):
    page.goto(f"{ERP_BASE_URL}/login", wait_until="networkidle")
    page.locator("#username").fill(ERP_USERNAME)
    page.locator("#password").fill(ERP_PASSWORD)
    page.get_by_role("button", name="Iniciar sesión").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)


def extract_links(page):
    return page.evaluate("""
    () => {
        const getText = (el) => (el.innerText || el.textContent || '').trim();

        return Array.from(document.querySelectorAll('a'))
            .map((el) => ({
                text: getText(el),
                href: el.href,
                classes: el.className
            }))
            .filter(item => item.text && item.href);
    }
    """)


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result = {
        "source": "playwright",
        "type": "erp_menu_map",
        "timestamp": timestamp,
        "base_url": ERP_BASE_URL,
        "modules": []
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        login(page)

        for module_name in MAIN_MODULES:
            print(f"📂 Explorando módulo: {module_name}")

            try:
                page.get_by_text(module_name, exact=True).click()
                page.wait_for_timeout(1200)

                links = extract_links(page)

                submodules = [
                    {
                        "name": link["text"],
                        "url": link["href"],
                        "classes": link["classes"]
                    }
                    for link in links
                    if link["text"] != "Dashboard"
                ]

                result["modules"].append({
                    "name": module_name,
                    "submodules": submodules
                })

            except Exception as e:
                result["modules"].append({
                    "name": module_name,
                    "error": str(e),
                    "submodules": []
                })

        output_file = OUTPUT_DIR / f"menu_map_{timestamp}.json"

        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        print("✅ Mapa de menú guardado:")
        print(output_file)

        browser.close()


if __name__ == "__main__":
    main()