from playwright.sync_api import sync_playwright

from src.config.profile_loader import load_profile
from src.extraction.navigator import ERPNavigator
from src.extraction.route_crawler import RouteCrawler


def main():
    profile = load_profile("cbmm")
    module_name = "Cuentas por cobrar"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        navigator = ERPNavigator(page, profile)

        print("Iniciando login...")
        navigator.login()

        crawler = RouteCrawler(page, profile)
        crawler.crawl_module(module_name)

        browser.close()


if __name__ == "__main__":
    main()