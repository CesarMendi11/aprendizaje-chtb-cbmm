from playwright.sync_api import sync_playwright

from src.config.profile_loader import load_profile
from src.extraction.navigator import ERPNavigator
from src.extraction.screen_extractor import ScreenExtractor


def main():

    profile = load_profile("cbmm")

    with sync_playwright() as p:

        browser = p.chromium.launch(headless=False)

        page = browser.new_page()

        navigator = ERPNavigator(page, profile)

        print("Iniciando login...")
        navigator.login()

        extractor = ScreenExtractor(page, profile)

        print("Extrayendo pantalla...")

        data = extractor.extract_screen_data()

        output_file = extractor.save_raw_json(data)

        print("✅ Extracción completada")
        print("Archivo:", output_file)

        browser.close()


if __name__ == "__main__":
    main()