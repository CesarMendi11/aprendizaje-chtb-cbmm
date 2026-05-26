import argparse

from playwright.sync_api import sync_playwright

from src.config.profile_loader import load_profile
from src.browser.navigator import ERPNavigator
from src.crawler.route_crawler import RouteCrawler
from src.auth.auth_manager import AuthManager

def main():
    parser = argparse.ArgumentParser(
        description="Crawler estructural ERP basado en perfil YAML"
    )

    parser.add_argument(
        "--profile",
        default="cbmm",
        help="Nombre del perfil YAML ubicado en configs/"
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ejecutar navegador en modo headless"
    )

    args = parser.parse_args()

    profile = load_profile(args.profile)
    modules = profile["exploration"].get("start_modules", [])

    if not modules:
        raise ValueError("No hay módulos definidos en exploration.start_modules")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        page = browser.new_page()

        auth = AuthManager(page, profile)
        navigator = ERPNavigator(page, profile)

        print("Iniciando login...")
        auth.login()

        for module_name in modules:
            print("=" * 60)
            print(f"Procesando módulo: {module_name}")
            print("=" * 60)

            crawler = RouteCrawler(page, profile)
            crawler.crawl_module(module_name)

            navigator.go_home()

        browser.close()

    print("✅ Perfil completo procesado")


if __name__ == "__main__":
    main()