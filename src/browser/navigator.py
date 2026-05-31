from __future__ import annotations

from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from src.auth.auth_manager import AuthManager


class ERPNavigator:
    """
    Capa de navegación con Playwright.

    Responsabilidad:
    - Abrir páginas.
    - Ejecutar login.
    - Ir al home.
    - Esperar cargas.
    - Obtener HTML, screenshot y URL actual.

    No decide qué rutas explorar.
    Esa responsabilidad será del RouteCrawler.
    """

    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile
        self.base_url = profile["erp"]["base_url"].rstrip("/")
        self.auth_manager = AuthManager(profile)

    def goto_path(self, path: str, wait_until: str = "domcontentloaded") -> None:
        full_url = urljoin(self.base_url + "/", path.lstrip("/"))
        self.page.goto(full_url, wait_until=wait_until)

    def login(self) -> None:
        login_config = self.profile["login"]
        credentials = self.auth_manager.get_credentials()
        login_url = self.auth_manager.get_login_url()

        self.page.goto(login_url, wait_until=login_config.get("wait_until", "domcontentloaded"))

        initial_wait_ms = login_config.get("initial_wait_ms", 1000)
        if initial_wait_ms:
            self.page.wait_for_timeout(initial_wait_ms)

        self.page.locator(login_config["username_selector"]).fill(credentials.username)
        self.page.locator(login_config["password_selector"]).fill(credentials.password)

        submit_selector = login_config.get("submit_selector")
        submit_role_name = login_config.get("submit_role_name")

        if submit_selector:
            self.page.locator(submit_selector).click()
        elif submit_role_name:
            self.page.get_by_role("button", name=submit_role_name).click()
        else:
            raise ValueError(
                "Debes definir login.submit_selector o login.submit_role_name en el YAML."
            )

        self.page.wait_for_load_state("domcontentloaded")

        post_login_wait_ms = login_config.get("post_login_wait_ms", 2000)
        if post_login_wait_ms:
            self.page.wait_for_timeout(post_login_wait_ms)

        if not self.auth_manager.is_successful_login_url(self.page.url):
            raise RuntimeError(
                "No se pudo confirmar login exitoso. "
                f"URL actual: {self.page.url}. "
                f"Se esperaba que contenga: {login_config.get('success_url_contains')}"
            )

    def goto_home(self) -> None:
        navigation = self.profile["navigation"]
        home_url = navigation["home_url"]

        self.goto_path(home_url)

        home_wait_ms = navigation.get("home_wait_ms", 1500)
        if home_wait_ms:
            self.page.wait_for_timeout(home_wait_ms)

    def current_url(self) -> str:
        return self.page.url

    def current_path(self) -> str:
        return self.page.evaluate("() => window.location.pathname")

    def get_html(self) -> str:
        return self.page.content()

    def get_title(self) -> str:
        return self.page.title()

    def screenshot_bytes(self, full_page: bool = True) -> bytes:
        return self.page.screenshot(full_page=full_page)

    def wait_for_manual_login(
        self,
        timeout_seconds: int = 120,
        check_interval_ms: int = 2000,
    ) -> bool:
        """
        Modo human-in-the-loop.

        Se usará luego si aparece 2FA, captcha o validación manual.
        Por ahora queda preparado.

        Retorna True si detecta login exitoso antes del timeout.
        """

        attempts = max(1, int((timeout_seconds * 1000) / check_interval_ms))

        for _ in range(attempts):
            if self.auth_manager.is_successful_login_url(self.page.url):
                return True

            self.page.wait_for_timeout(check_interval_ms)

        return False

    def click_text_if_visible(self, text: str, exact: bool = False, timeout_ms: int = 2000) -> bool:
        """
        Click simple por texto visible.

        Esto no reemplaza al crawler.
        Solo sirve para casos puntuales como abrir un módulo conocido.
        """

        try:
            locator = self.page.get_by_text(text, exact=exact).first
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.click()
            self.page.wait_for_timeout(1000)
            return True
        except PlaywrightTimeoutError:
            return False
        except Exception:
            return False