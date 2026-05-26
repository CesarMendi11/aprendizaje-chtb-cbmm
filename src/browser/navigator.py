from urllib.parse import urljoin

from playwright.sync_api import Page


class ERPNavigator:
    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile
        self.base_url = profile["erp"]["base_url"]

    def go_home(self) -> None:
        home_url = urljoin(
            self.base_url,
            self.profile["navigation"]["home_url"],
        )

        self.page.goto(home_url, wait_until="networkidle")
        self.page.wait_for_timeout(1500)

    def open_module(self, module_name: str) -> None:
        self.page.get_by_text(module_name, exact=True).click()
        self.page.wait_for_timeout(1200)

    def open_module_safe(self, module_name: str) -> bool:
        try:
            locator = self.page.get_by_text(module_name, exact=True)
            locator.first.click()
            self.page.wait_for_timeout(1500)
            return True
        except Exception as error:
            print(f"⚠️ No se pudo abrir módulo {module_name}: {error}")
            return False