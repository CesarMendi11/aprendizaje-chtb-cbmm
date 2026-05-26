from playwright.sync_api import sync_playwright

from src.auth.auth_manager import AuthManager
from src.browser.navigator import ERPNavigator
from src.config.profile_loader import load_profile
from src.discovery.menu_discovery import MenuDiscovery


def main():
    profile = load_profile("cbmm")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        auth = AuthManager(page, profile)
        navigator = ERPNavigator(page, profile)

        print("Iniciando login...")
        auth.login()

        navigator.go_home()

        menu_discovery = MenuDiscovery(page, profile)
        items = menu_discovery.extract_menu_items()

        print(f"Elementos encontrados en menú: {len(items)}")

        for item in items:
            print("-" * 60)
            print("text:", item.get("text"))
            print("href:", item.get("href"))
            print("tag:", item.get("tag"))
            print("classes:", item.get("classes"))

        browser.close()


if __name__ == "__main__":
    main()