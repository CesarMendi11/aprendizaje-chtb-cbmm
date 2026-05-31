from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urljoin


@dataclass(frozen=True)
class ERPCredentials:
    username: str
    password: str


class AuthManager:
    """
    Maneja la configuración de autenticación del ERP.

    Responsabilidad:
    - Obtener credenciales desde .env o YAML.
    - Construir URL de login.
    - Validar si una URL corresponde a login exitoso.

    Este componente NO ejecuta Playwright directamente.
    """

    def __init__(self, profile: dict):
        self.profile = profile
        self.base_url = profile["erp"]["base_url"].rstrip("/")
        self.login_config = profile["login"]

    def get_login_url(self) -> str:
        login_path = self.login_config["url"].lstrip("/")
        return urljoin(self.base_url + "/", login_path)

    def get_credentials(self) -> ERPCredentials:
        username_env = self.login_config.get("username_env", "ERP_USERNAME")
        password_env = self.login_config.get("password_env", "ERP_PASSWORD")

        username = os.getenv(username_env) or self.login_config.get("username")
        password = os.getenv(password_env) or self.login_config.get("password")

        if not username or not password:
            raise RuntimeError(
                "No se encontraron credenciales. "
                "Define ERP_USERNAME y ERP_PASSWORD en .env "
                "o username/password en el YAML."
            )

        return ERPCredentials(username=username, password=password)

    def is_successful_login_url(self, current_url: str) -> bool:
        expected_fragment = self.login_config.get("success_url_contains")

        if not expected_fragment:
            return True

        return expected_fragment in current_url

    def requires_submit_selector(self) -> bool:
        return bool(self.login_config.get("submit_selector"))

    def requires_submit_role_name(self) -> bool:
        return bool(self.login_config.get("submit_role_name"))