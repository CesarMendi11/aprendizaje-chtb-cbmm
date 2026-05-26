import os
from urllib.parse import urljoin

from dotenv import load_dotenv
from playwright.sync_api import Page

load_dotenv()


class AuthManager:
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
            name=login_config["submit_role_name"],
        ).click()

        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(3000)

        if not self.is_login_successful():
            raise RuntimeError(
                f"Login posiblemente falló. URL actual: {self.page.url}"
            )

    def is_login_successful(self) -> bool:
        login_config = self.profile["login"]
        success_text = login_config.get("success_url_contains")

        if not success_text:
            return True

        return success_text in self.page.url