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

    page.screenshot(
        path=OUTPUT_DIR / f"after_login_{timestamp}.png",
        full_page=True
    )

    data = {
        "url": page.url,
        "title": page.title(),
        "text": page.inner_text("body"),
        "timestamp": timestamp
    }

    with open(OUTPUT_DIR / f"after_login_{timestamp}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("✅ Login probado y captura guardada en data/raw/playwright/")
    print(f"URL actual: {page.url}")

    browser.close()