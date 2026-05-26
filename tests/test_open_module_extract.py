from playwright.sync_api import sync_playwright

from src.auth.auth_manager import AuthManager
from src.config.profile_loader import load_profile
from src.browser.navigator import ERPNavigator
from src.extraction.screen_extractor import ScreenExtractor


def main():
    profile = load_profile("cbmm")

    module_name = "Cuentas por cobrar"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        auth = AuthManager(page, profile)
        navigator = ERPNavigator(page, profile)

        print("Iniciando login...")
        auth.login()

        print(f"Abriendo módulo: {module_name}")
        opened = navigator.open_module_safe(module_name)

        if not opened:
            print("❌ No se pudo abrir el módulo.")
            browser.close()
            return

        extractor = ScreenExtractor(page, profile)

        print("Extrayendo pantalla después de abrir módulo...")
        data = extractor.extract_screen_data()
        output_file = extractor.save_raw_json(data)

        print("✅ Extracción completada")
        print("Archivo:", output_file)

        browser.close()


if __name__ == "__main__":
    main()