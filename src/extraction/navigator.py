import os
from urllib.parse import urljoin

from dotenv import load_dotenv
from playwright.sync_api import Page

load_dotenv()


class ERPNavigator:
    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile
        self.base_url = profile["erp"]["base_url"]

    def login(self) -> None:
        login_config = self.profile["login"]

        username = os.getenv("ERP_USERNAME")
        password = os.getenv("ERP_PASSWORD")

        if not username or not password:
            raise ValueError("Faltan ERP_USERNAME o ERP_PASSWORD en el archivo .env")

        login_url = urljoin(self.base_url, login_config["url"])

        self.page.goto(login_url, wait_until="networkidle")

        self.page.locator(login_config["username_selector"]).fill(username)
        self.page.locator(login_config["password_selector"]).fill(password)

        self.page.get_by_role(
            "button",
            name=login_config["submit_role_name"]
        ).click()

        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(3000)

        success_text = login_config.get("success_url_contains")

        if success_text and success_text not in self.page.url:
            raise RuntimeError(
                f"Login posiblemente falló. URL actual: {self.page.url}"
            )

    def go_home(self) -> None:
        home_url = urljoin(
            self.base_url,
            self.profile["navigation"]["home_url"]
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