from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ProfileLoader:
    """
    Carga y valida el perfil YAML del ERP.

    Responsabilidad:
    - Leer configs/cbmm.yaml.
    - Validar secciones mínimas.
    - Cargar variables de entorno desde .env.
    """

    def __init__(self, profile_path: str | Path):
        self.profile_path = Path(profile_path)

    def load(self) -> dict[str, Any]:
        if not self.profile_path.exists():
            raise FileNotFoundError(f"No existe el perfil YAML: {self.profile_path}")

        load_dotenv()

        with self.profile_path.open("r", encoding="utf-8") as file:
            profile = yaml.safe_load(file)

        if not isinstance(profile, dict):
            raise ValueError("El YAML debe contener un objeto principal.")

        self._validate(profile)
        return profile

    def _validate(self, profile: dict[str, Any]) -> None:
        required_sections = [
            "erp",
            "login",
            "navigation",
            "exploration",
            "safety",
            "extraction",
            "output",
        ]

        for section in required_sections:
            if section not in profile:
                raise ValueError(f"Falta la sección requerida: {section}")

        for field in ["name", "code", "base_url"]:
            if field not in profile["erp"]:
                raise ValueError(f"Falta erp.{field}")

        for field in [
            "url",
            "username_selector",
            "password_selector",
            "success_url_contains",
        ]:
            if field not in profile["login"]:
                raise ValueError(f"Falta login.{field}")

        if not profile["login"].get("submit_selector") and not profile["login"].get(
            "submit_role_name"
        ):
            raise ValueError(
                "Debes definir login.submit_selector o login.submit_role_name."
            )

        if "home_url" not in profile["navigation"]:
            raise ValueError("Falta navigation.home_url")

        allowed_routes = profile["exploration"].get("allowed_routes", [])
        blocked_routes = profile["exploration"].get("blocked_routes", [])

        if not isinstance(allowed_routes, list):
            raise ValueError("exploration.allowed_routes debe ser una lista.")

        if not isinstance(blocked_routes, list):
            raise ValueError("exploration.blocked_routes debe ser una lista.")

        required_output_fields = [
            "raw_playwright_dir",
            "html_dir",
            "screenshots_dir",
            "processed_structural_dir",
            "review_structural_dir",
        ]

        for field in required_output_fields:
            if field not in profile["output"]:
                raise ValueError(f"Falta output.{field}")