from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.models.ui_event import UIEventType


class ProfileLoader:
    """
    Carga y valida el perfil YAML del ERP.

    Responsabilidad:
    - Leer el perfil YAML solicitado.
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

        safety = profile.get("safety", {})
        default_decision = safety.get("default_decision", "deny")
        if default_decision not in {"allow", "review", "deny"}:
            raise ValueError(
                "safety.default_decision debe ser allow, review o deny."
            )

        category_fields = [
            "allowed_event_categories",
            "review_event_categories",
            "forbidden_event_categories",
        ]
        valid_event_categories = {item.value for item in UIEventType}
        for field in category_fields:
            value = safety.get(field)
            if value is not None and not isinstance(value, list):
                raise ValueError(f"safety.{field} debe ser una lista.")
            if isinstance(value, list):
                unknown = sorted(set(value) - valid_event_categories)
                if unknown:
                    raise ValueError(
                        f"safety.{field} contiene categorías desconocidas: {unknown}"
                    )


        ui_events = profile.get("ui_events", {})

        max_event_depth = ui_events.get("max_event_depth")
        if max_event_depth is not None and (
            not isinstance(max_event_depth, int) or max_event_depth < 0
        ):
            raise ValueError(
                "ui_events.max_event_depth debe ser un entero no negativo."
            )

        for field in [
            "home_navigation_enabled",
            "explore_local_route_roots",
            "recursive_state_exploration",
        ]:
            value = ui_events.get(field)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"ui_events.{field} debe ser booleano.")

        for field in ["home_event_categories", "local_event_categories"]:
            value = ui_events.get(field)
            if value is not None and not isinstance(value, list):
                raise ValueError(f"ui_events.{field} debe ser una lista.")
            if isinstance(value, list):
                unknown = sorted(set(value) - valid_event_categories)
                if unknown:
                    raise ValueError(
                        f"ui_events.{field} contiene categorías desconocidas: "
                        f"{unknown}"
                    )

        exploration_budget = ui_events.get("exploration_budget", {})
        if exploration_budget and not isinstance(exploration_budget, dict):
            raise ValueError("ui_events.exploration_budget debe ser un objeto.")

        exclude_global = exploration_budget.get(
            "exclude_global_navigation_outside_home"
        )
        if exclude_global is not None and not isinstance(exclude_global, bool):
            raise ValueError(
                "ui_events.exploration_budget."
                "exclude_global_navigation_outside_home debe ser booleano."
            )

        for field in ["category_limits", "home_category_limits"]:
            limits = exploration_budget.get(field, {})
            if limits and not isinstance(limits, dict):
                raise ValueError(
                    f"ui_events.exploration_budget.{field} debe ser un objeto."
                )
            if isinstance(limits, dict):
                unknown = sorted(set(limits) - valid_event_categories)
                if unknown:
                    raise ValueError(
                        f"ui_events.exploration_budget.{field} contiene "
                        f"categorías desconocidas: {unknown}"
                    )
                invalid_limits = {
                    key: value
                    for key, value in limits.items()
                    if not isinstance(value, int) or value < 0
                }
                if invalid_limits:
                    raise ValueError(
                        f"ui_events.exploration_budget.{field} solo admite "
                        "enteros no negativos."
                    )

        state_detection = profile.get("state_detection", {})
        volatile_patterns = state_detection.get("volatile_text_patterns")
        if volatile_patterns is not None and not isinstance(volatile_patterns, list):
            raise ValueError(
                "state_detection.volatile_text_patterns debe ser una lista."
            )

        navigation_state_routes = state_detection.get("navigation_state_routes")
        if navigation_state_routes is not None:
            if not isinstance(navigation_state_routes, list):
                raise ValueError(
                    "state_detection.navigation_state_routes debe ser una lista."
                )
            if not all(isinstance(route, str) for route in navigation_state_routes):
                raise ValueError(
                    "state_detection.navigation_state_routes solo admite rutas de texto."
                )

        stability = state_detection.get("stability", {})
        if stability and not isinstance(stability, dict):
            raise ValueError("state_detection.stability debe ser un objeto.")
        enabled = stability.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("state_detection.stability.enabled debe ser booleano.")
        for field in [
            "timeout_ms",
            "interval_ms",
            "minimum_observation_ms",
            "required_consecutive_samples",
        ]:
            value = stability.get(field)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(
                    f"state_detection.stability.{field} debe ser un entero no negativo."
                )
        required_samples = stability.get("required_consecutive_samples")
        if required_samples == 0:
            raise ValueError(
                "state_detection.stability.required_consecutive_samples debe ser mayor que cero."
            )


        extraction = profile.get("extraction", {})
        regions = extraction.get("regions", {})
        if regions and not isinstance(regions, dict):
            raise ValueError("extraction.regions debe ser un objeto.")

        for region_name, selectors in regions.items():
            if not isinstance(selectors, list):
                raise ValueError(
                    f"extraction.regions.{region_name} debe ser una lista."
                )
            if not all(isinstance(selector, str) for selector in selectors):
                raise ValueError(
                    f"extraction.regions.{region_name} solo admite selectores de texto."
                )

        title_resolution = extraction.get("title_resolution", {})
        if title_resolution and not isinstance(title_resolution, dict):
            raise ValueError("extraction.title_resolution debe ser un objeto.")

        route_titles = title_resolution.get("route_titles", {})
        if route_titles and not isinstance(route_titles, dict):
            raise ValueError(
                "extraction.title_resolution.route_titles debe ser un objeto."
            )

        generic_titles = title_resolution.get("generic_document_titles", [])
        if generic_titles and not isinstance(generic_titles, list):
            raise ValueError(
                "extraction.title_resolution.generic_document_titles debe ser una lista."
            )

        state_replay = profile.get("state_replay", {})
        if state_replay and not isinstance(state_replay, dict):
            raise ValueError("state_replay debe ser un objeto.")

        for field in [
            "page_wait_ms",
            "step_wait_ms",
            "click_timeout_ms",
            "restore_attempts",
        ]:
            value = state_replay.get(field)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"state_replay.{field} debe ser un entero no negativo.")

        for field in ["enabled", "verify_each_step"]:
            value = state_replay.get(field)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"state_replay.{field} debe ser booleano.")
