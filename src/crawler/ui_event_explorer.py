from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from src.crawler.state_signature import StateSignatureBuilder
from src.discovery.event_candidate_discovery import EventCandidate, EventCandidateDiscovery
from src.extraction.screen_extractor import ScreenExtractor


@dataclass
class UIEventResult:
    """
    Resultado de probar un evento UI sobre un candidato.

    changed:
        True si el click produjo un cambio detectable en la interfaz.

    before_fingerprint / after_fingerprint:
        Firmas del estado antes y después.

    before_route / after_route:
        Rutas antes y después. Pueden ser iguales si solo cambió el DOM.
    """

    candidate: dict[str, Any]
    changed: bool
    before_fingerprint: str
    after_fingerprint: str
    before_exact_fingerprint: str
    after_exact_fingerprint: str
    before_route: str
    after_route: str
    after_screen_data: dict[str, Any]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "changed": self.changed,
            "before_fingerprint": self.before_fingerprint,
            "after_fingerprint": self.after_fingerprint,
            "before_exact_fingerprint": self.before_exact_fingerprint,
            "after_exact_fingerprint": self.after_exact_fingerprint,
            "before_route": self.before_route,
            "after_route": self.after_route,
            "after_screen_data": self.after_screen_data,
            "error": self.error,
        }


class UIEventExplorer:
    """
    Explorador de eventos UI estilo Crawljax.

    Responsabilidad:
    - Recibir una pantalla ya extraída.
    - Buscar candidatos seguros.
    - Ejecutar clicks controlados con Playwright.
    - Comparar estados antes/después.
    - Reportar cambios.

    Este componente NO decide persistencia final.
    Este componente NO llama al LLM.
    Este componente NO llena formularios.
    """

    def __init__(
        self,
        page: Page,
        profile: dict[str, Any],
        extractor: ScreenExtractor,
        candidate_discovery: EventCandidateDiscovery,
        state_signature_builder: StateSignatureBuilder,
    ):
        self.page = page
        self.profile = profile
        self.extractor = extractor
        self.candidate_discovery = candidate_discovery
        self.state_signature_builder = state_signature_builder

        ui_events = profile.get("ui_events", {})
        candidate_limits = ui_events.get("candidate_limits", {})

        self.enabled = ui_events.get("enabled", True)
        self.event_wait_ms = ui_events.get("event_wait_ms", 800)
        self.click_timeout_ms = ui_events.get("click_timeout_ms", 2500)
        self.max_events_per_state = candidate_limits.get("max_events_per_state", 25)

        # Los href normales ya los maneja LinkDiscovery + Frontier.
        # Aquí nos enfocamos en cambios de estado UI sin depender de URL.
        self.skip_link_navigation = ui_events.get("skip_link_navigation", True)

    def explore_current_state(
        self,
        screen_data: dict[str, Any] | None = None,
    ) -> list[UIEventResult]:
        if not self.enabled:
            return []

        current_screen_data = screen_data or self.extractor.extract()
        current_signature = self.state_signature_builder.build(current_screen_data)

        candidates = self.candidate_discovery.discover_safe_candidates(
            current_screen_data
        )

        candidates = self._filter_candidates_for_ui_events(candidates)
        candidates = candidates[: self.max_events_per_state]

        results: list[UIEventResult] = []

        for candidate in candidates:
            result = self._try_candidate(
                candidate=candidate,
                before_screen_data=current_screen_data,
                before_fingerprint=current_signature.structural_fingerprint,
                before_exact_fingerprint=current_signature.exact_fingerprint,
                before_route=current_signature.route,
            )

            results.append(result)

            if result.changed:
                current_screen_data = result.after_screen_data
                current_signature = self.state_signature_builder.build(
                    current_screen_data
                )

        return results

    def _filter_candidates_for_ui_events(
        self,
        candidates: list[EventCandidate],
    ) -> list[EventCandidate]:
        filtered = []

        for candidate in candidates:
            if not candidate.selector:
                continue

            if candidate.dangerous:
                continue

            if self.skip_link_navigation and (
                candidate.action_kind == "link_navigation"
                or candidate.event_category == "navigation_link"
            ):
                continue

            filtered.append(candidate)

        return filtered

    def _try_candidate(
        self,
        candidate: EventCandidate,
        before_screen_data: dict[str, Any],
        before_fingerprint: str,
        before_exact_fingerprint: str,
        before_route: str,
    ) -> UIEventResult:
        try:
            self._close_possible_overlays()

            locator = self.page.locator(candidate.selector).first
            locator.wait_for(state="visible", timeout=self.click_timeout_ms)
            locator.click(timeout=self.click_timeout_ms)

            if self.event_wait_ms:
                self.page.wait_for_timeout(self.event_wait_ms)

            after_screen_data = self.extractor.extract()
            after_signature = self.state_signature_builder.build(after_screen_data)

            changed = before_fingerprint != after_signature.structural_fingerprint

            return UIEventResult(
                candidate=candidate.to_dict(),
                changed=changed,
                before_fingerprint=before_fingerprint,
                after_fingerprint=after_signature.structural_fingerprint,
                before_exact_fingerprint=before_exact_fingerprint,
                after_exact_fingerprint=after_signature.exact_fingerprint,
                before_route=before_route,
                after_route=after_signature.route,
                after_screen_data=after_screen_data,
                error=None,
            )

        except PlaywrightTimeoutError as error:
            return self._error_result(
                candidate=candidate,
                before_fingerprint=before_fingerprint,
                before_exact_fingerprint=before_exact_fingerprint,
                before_route=before_route,
                before_screen_data=before_screen_data,
                error=f"timeout: {error}",
            )

        except Exception as error:
            return self._error_result(
                candidate=candidate,
                before_fingerprint=before_fingerprint,
                before_exact_fingerprint=before_exact_fingerprint,
                before_route=before_route,
                before_screen_data=before_screen_data,
                error=str(error),
            )

    def _close_possible_overlays(self) -> None:
        """
        Cierra menús flotantes o overlays abiertos antes de probar otro candidato.

        Esto es genérico:
        - menús de usuario
        - dropdowns
        - tooltips
        - overlays de Angular Material/CDK
        """
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(100)
        except Exception:
            pass

    def _error_result(
        self,
        candidate: EventCandidate,
        before_fingerprint: str,
        before_exact_fingerprint: str,
        before_route: str,
        before_screen_data: dict[str, Any],
        error: str,
    ) -> UIEventResult:
        return UIEventResult(
            candidate=candidate.to_dict(),
            changed=False,
            before_fingerprint=before_fingerprint,
            after_fingerprint=before_fingerprint,
            before_exact_fingerprint=before_exact_fingerprint,
            after_exact_fingerprint=before_exact_fingerprint,
            before_route=before_route,
            after_route=before_route,
            after_screen_data=before_screen_data,
            error=error,
        )