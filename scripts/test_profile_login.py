from pathlib import Path
from datetime import datetime

from playwright.sync_api import sync_playwright

from src.auth.auth_manager import AuthManager
from src.config.profile_loader import load_profile
from src.browser.navigator import ERPNavigator


def main():
    profile = load_profile("cbmm")

    output_dir = Path(profile["output"]["screenshots_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        auth = AuthManager(page, profile)
        navigator = ERPNavigator(page, profile)

        print("Iniciando login...")
        auth.login()

        screenshot_path = output_dir / f"profile_login_{timestamp}.png"
        page.screenshot(path=screenshot_path, full_page=True)

        print("✅ Login exitoso usando perfil YAML")
        print("URL actual:", page.url)
        print("Screenshot:", screenshot_path)

        browser.close()


if __name__ == "__main__":
    main()