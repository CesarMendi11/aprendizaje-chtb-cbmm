from pathlib import Path
from typing import Any

import yaml


class ProfileLoaderError(Exception):
    pass


def load_profile(profile_name: str) -> dict[str, Any]:
    """
    Load an ERP profile from configs/<profile_name>.yaml
    """
    profile_path = Path("configs") / f"{profile_name}.yaml"

    if not profile_path.exists():
        raise ProfileLoaderError(f"No existe el perfil: {profile_path}")

    with open(profile_path, "r", encoding="utf-8") as file:
        profile = yaml.safe_load(file)

    validate_profile(profile)

    return profile


def validate_profile(profile: dict[str, Any]) -> None:
    required_sections = [
        "erp",
        "login",
        "navigation",
        "exploration",
        "extraction",
        "safety",
        "output",
    ]

    for section in required_sections:
        if section not in profile:
            raise ProfileLoaderError(f"Falta la sección obligatoria: {section}")

    required_login_fields = [
        "url",
        "username_selector",
        "password_selector",
        "submit_role_name",
    ]

    for field in required_login_fields:
        if field not in profile["login"]:
            raise ProfileLoaderError(f"Falta login.{field}")

    if "base_url" not in profile["erp"]:
        raise ProfileLoaderError("Falta erp.base_url")