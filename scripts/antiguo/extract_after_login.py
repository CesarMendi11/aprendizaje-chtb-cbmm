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

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_elements(page):
    return page.evaluate("""
    () => {
        const getText = (el) => (el.innerText || el.textContent || '').trim();

        const clean = (items) => {
            const seen = new Set();
            return items
                .filter(item => item.text && item.text.length > 1)
                .filter(item => {
                    const key = item.text + '|' + item.tag + '|' + item.href;
                    if (seen.has(key)) return false;
                    seen.add(key);
                    return true;
                });
        };

        const buttons = Array.from(document.querySelectorAll('button')).map((el) => ({
            tag: el.tagName.toLowerCase(),
            text: getText(el),
            type: el.getAttribute('type'),
            classes: el.className,
            disabled: el.disabled
        }));

        const inputs = Array.from(document.querySelectorAll('input, textarea, select')).map((el) => ({
            tag: el.tagName.toLowerCase(),
            id: el.id,
            name: el.getAttribute('name'),
            type: el.getAttribute('type'),
            placeholder: el.getAttribute('placeholder'),
            required: el.required || false
        }));

        const links = Array.from(document.querySelectorAll('a')).map((el) => ({
            tag: el.tagName.toLowerCase(),
            text: getText(el),
            href: el.href,
            classes: el.className
        }));

        const possible_menu_items = Array.from(document.querySelectorAll(`
            a,
            button,
            div,
            span,
            mat-list-item,
            mat-expansion-panel,
            mat-expansion-panel-header,
            fuse-vertical-navigation-item,
            fuse-horizontal-navigation-item
        `)).map((el) => ({
            tag: el.tagName.toLowerCase(),
            text: getText(el),
            href: el.href || null,
            classes: el.className || null,
            role: el.getAttribute('role'),
            aria_label: el.getAttribute('aria-label')
        }));

        return {
            buttons: clean(buttons),
            inputs,
            links: clean(links),
            possible_menu_items: clean(possible_menu_items)
        };
    }
    """)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    login_url = f"{ERP_BASE_URL}/login"
    page.goto(login_url, wait_until="networkidle")

    page.locator("#username").fill(ERP_USERNAME)
    page.locator("#password").fill(ERP_PASSWORD)
    page.get_by_role("button", name="Iniciar sesión").click()

    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    
    # Abrir módulo
    page.get_by_text("Cuentas por cobrar", exact=True).click()

    page.wait_for_timeout(2000)

    page.screenshot(
        path=OUTPUT_DIR / f"cuentas_por_cobrar_{timestamp}.png",
        full_page=True
    )

    print(page.inner_text("body"))

    screenshot_path = OUTPUT_DIR / f"home_{timestamp}.png"
    page.screenshot(path=screenshot_path, full_page=True)

    extracted = {
        "source": "playwright",
        "page_type": "home_after_login",
        "url": page.url,
        "title": page.title(),
        "visible_text": page.inner_text("body"),
        "timestamp": timestamp,
        "screenshot": str(screenshot_path),
        "elements": extract_elements(page)
    }

    output_file = OUTPUT_DIR / f"home_{timestamp}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(extracted, f, ensure_ascii=False, indent=2)

    print("✅ Extracción guardada:")
    print(output_file)
    print("📸 Screenshot:")
    print(screenshot_path)

    browser.close()