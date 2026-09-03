from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from erp_assistant.acquisition.discovery.event_candidate_discovery import EventCandidate


@dataclass(frozen=True)
class SandboxAuthorization:
    """Resultado explícito para un opener denegado por la política base."""

    allowed: bool
    reasons: tuple[str, ...]


class SandboxExplorationPolicy:
    """Excepción acotada para observar openers en un ERP de pruebas.

    La política base permanece intacta: un ``mutative_action`` continúa DENY.
    Esta capa solo autoriza el clic observacional cuando el perfil declara
    simultáneamente ``environment=test`` y ``strategy=test_full`` y el candidato
    cumple las reglas del sandbox. Nunca autoriza submit ni acciones fuera del
    estado raíz local.
    """

    DEFAULT_BLOCKED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, profile: dict[str, Any]):
        crawl_mode = profile.get("crawl_mode", {}) or {}
        config = profile.get("sandbox_exploration", {}) or {}

        self.environment = str(crawl_mode.get("environment") or "production").lower()
        self.strategy = str(crawl_mode.get("strategy") or "safe").lower()
        self.enabled = bool(config.get("enabled", False))
        self.active = self.enabled and self.environment == "test" and self.strategy == "test_full"

        self.root_states_only = bool(config.get("root_states_only", True))
        self.max_openers_per_root_state = max(
            0,
            int(config.get("max_openers_per_root_state", 1)),
        )
        self.allowed_regions = {
            str(value).strip()
            for value in config.get("allowed_regions", ["main_content"])
            if str(value).strip()
        }
        self.opener_label_prefixes = tuple(
            self._normalize(value)
            for value in config.get("opener_label_prefixes", [])
            if self._normalize(value)
        )
        self.blocked_http_methods = {
            str(value).upper().strip()
            for value in config.get(
                "blocked_http_methods",
                sorted(self.DEFAULT_BLOCKED_METHODS),
            )
            if str(value).strip()
        }

    def evaluate(
        self,
        candidate: EventCandidate,
        *,
        source_state_depth: int | None,
        is_home_route: bool,
    ) -> SandboxAuthorization:
        reasons: list[str] = []

        if not self.active:
            return SandboxAuthorization(False, ("sandbox_not_active",))

        if is_home_route:
            return SandboxAuthorization(False, ("sandbox_home_route_blocked",))

        if self.root_states_only and source_state_depth != 0:
            return SandboxAuthorization(False, ("sandbox_root_state_only",))

        if candidate.event_category != "mutative_action":
            return SandboxAuthorization(False, ("sandbox_requires_mutative_opener",))

        if candidate.decision != "deny" or not candidate.dangerous:
            return SandboxAuthorization(False, ("sandbox_requires_base_policy_deny",))

        if str(candidate.metadata.get("type") or "").strip().casefold() == "submit":
            return SandboxAuthorization(False, ("sandbox_submit_blocked",))

        region = str(candidate.metadata.get("region") or "main_content")
        if region not in self.allowed_regions:
            return SandboxAuthorization(False, ("sandbox_region_not_allowed",))

        label = self._normalize(candidate.label)
        if not label:
            return SandboxAuthorization(False, ("sandbox_missing_label",))

        if not any(label.startswith(prefix) for prefix in self.opener_label_prefixes):
            return SandboxAuthorization(False, ("sandbox_label_not_opener",))

        reasons.extend(
            [
                "sandbox_test_environment",
                "sandbox_test_full_strategy",
                "sandbox_root_opener_match",
                "sandbox_submit_not_present",
            ]
        )
        return SandboxAuthorization(True, tuple(reasons))

    @staticmethod
    def _normalize(value: Any) -> str:
        text = " ".join(str(value or "").split()).strip().casefold()
        decomposed = unicodedata.normalize("NFKD", text)
        return "".join(char for char in decomposed if not unicodedata.combining(char))
