from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

from src.crawler.state_restorer import RestoreResult, StateRestorer
from src.crawler.state_signature import StateSignatureBuilder
from src.discovery.event_candidate_discovery import (
    EventCandidate,
    EventCandidateDiscovery,
)
from src.extraction.screen_extractor import ScreenExtractor
from src.models.ui_event import UIEvent
from src.models.ui_state import UIState


@dataclass
class UIEventResult:
    """Resultado de ejecutar de forma controlada un candidato UI."""

    event: UIEvent
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
    source_state_id: str | None = None
    target_state_id: str | None = None
    restored_before: bool = False
    restore_strategy: str | None = None
    restore_error: str | None = None
    artifact_error: str | None = None
    after_html: str | None = field(default=None, repr=False)
    after_screenshot: bytes | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event.to_dict(),
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
            "source_state_id": self.source_state_id,
            "target_state_id": self.target_state_id,
            "restored_before": self.restored_before,
            "restore_strategy": self.restore_strategy,
            "restore_error": self.restore_error,
            "artifact_error": self.artifact_error,
            "after_html_captured": self.after_html is not None,
            "after_screenshot_captured": self.after_screenshot is not None,
        }


class UIEventExplorer:
    """
    Explorador de eventos UI inspirado en Crawljax.

    Cuando recibe un ``UIState`` y un ``StateRestorer``, restaura el estado
    fuente antes de cada candidato. Así evita que los clics se acumulen y que el
    grafo atribuya una transición al estado equivocado.
    """

    def __init__(
        self,
        page: Page,
        profile: dict[str, Any],
        extractor: ScreenExtractor,
        candidate_discovery: EventCandidateDiscovery,
        state_signature_builder: StateSignatureBuilder,
        state_restorer: StateRestorer | None = None,
    ):
        self.page = page
        self.profile = profile
        self.extractor = extractor
        self.candidate_discovery = candidate_discovery
        self.state_signature_builder = state_signature_builder
        self.state_restorer = state_restorer

        ui_events = profile.get("ui_events", {})
        candidate_limits = ui_events.get("candidate_limits", {})

        self.enabled = ui_events.get("enabled", True)
        self.event_wait_ms = ui_events.get("event_wait_ms", 800)
        self.click_timeout_ms = ui_events.get("click_timeout_ms", 2500)
        self.max_events_per_state = candidate_limits.get(
            "max_events_per_state", 25
        )
        self.skip_link_navigation = ui_events.get("skip_link_navigation", True)
        self.restore_after_exploration = ui_events.get(
            "restore_after_exploration", True
        )
        self.capture_event_artifacts = ui_events.get(
            "capture_event_artifacts", True
        )
        self.artifact_timeout_ms = ui_events.get(
            "artifact_timeout_ms", 3000
        )

    def set_state_restorer(self, restorer: StateRestorer | None) -> None:
        self.state_restorer = restorer

    def explore_current_state(
        self,
        screen_data: dict[str, Any] | None = None,
        source_state: UIState | None = None,
    ) -> list[UIEventResult]:
        if not self.enabled:
            return []

        current_screen_data = screen_data or self.extractor.extract()
        current_signature = self.state_signature_builder.build(
            current_screen_data
        )

        candidates = self.candidate_discovery.discover_exploration_candidates(
            current_screen_data
        )
        candidates = self._filter_candidates_for_ui_events(candidates)
        candidates = candidates[: self.max_events_per_state]

        isolated = source_state is not None and self.state_restorer is not None
        results: list[UIEventResult] = []

        for candidate in candidates:
            restore_result: RestoreResult | None = None

            if isolated:
                restore_result = self.state_restorer.restore(source_state)
                if not restore_result.success:
                    results.append(
                        self._restoration_error_result(
                            candidate=candidate,
                            source_state=source_state,
                            restore_result=restore_result,
                        )
                    )
                    continue

                before_screen_data = restore_result.screen_data
                before_signature = restore_result.signature
                if before_signature is None:
                    before_signature = self.state_signature_builder.build(
                        before_screen_data
                    )
            else:
                before_screen_data = current_screen_data
                before_signature = current_signature

            result = self._try_candidate(
                candidate=candidate,
                before_screen_data=before_screen_data,
                before_fingerprint=before_signature.structural_fingerprint,
                before_exact_fingerprint=before_signature.exact_fingerprint,
                before_route=before_signature.route,
                source_state_id=(source_state.state_id if source_state else None),
                restore_result=restore_result,
            )
            results.append(result)

            # Compatibilidad con el comportamiento previo cuando todavía no se
            # dispone de un restaurador. El modo aislado nunca acumula estados.
            if result.changed and not isolated:
                current_screen_data = result.after_screen_data
                current_signature = self.state_signature_builder.build(
                    current_screen_data
                )

        if isolated and self.restore_after_exploration:
            self.state_restorer.restore(source_state)

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
        source_state_id: str | None = None,
        restore_result: RestoreResult | None = None,
    ) -> UIEventResult:
        event = candidate.to_ui_event()

        try:
            locator = self.page.locator(candidate.selector).first
            locator.wait_for(state="visible", timeout=self.click_timeout_ms)
            locator.click(timeout=self.click_timeout_ms)

            if self.event_wait_ms:
                self.page.wait_for_timeout(self.event_wait_ms)

            after_screen_data = self.extractor.extract()
            after_signature = self.state_signature_builder.build(
                after_screen_data
            )
            changed = (
                before_fingerprint
                != after_signature.structural_fingerprint
            )

            if changed:
                after_html, after_screenshot, artifact_error = (
                    self._capture_result_artifacts()
                )
            else:
                after_html, after_screenshot, artifact_error = (
                    None,
                    None,
                    None,
                )

            return UIEventResult(
                event=event,
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
                source_state_id=source_state_id,
                restored_before=restore_result is not None,
                restore_strategy=(
                    restore_result.strategy if restore_result else None
                ),
                artifact_error=artifact_error,
                after_html=after_html,
                after_screenshot=after_screenshot,
            )

        except PlaywrightTimeoutError as error:
            return self._error_result(
                candidate=candidate,
                before_fingerprint=before_fingerprint,
                before_exact_fingerprint=before_exact_fingerprint,
                before_route=before_route,
                before_screen_data=before_screen_data,
                error=f"timeout: {error}",
                source_state_id=source_state_id,
                restore_result=restore_result,
            )
        except Exception as error:
            return self._error_result(
                candidate=candidate,
                before_fingerprint=before_fingerprint,
                before_exact_fingerprint=before_exact_fingerprint,
                before_route=before_route,
                before_screen_data=before_screen_data,
                error=str(error),
                source_state_id=source_state_id,
                restore_result=restore_result,
            )

    def _capture_result_artifacts(
        self,
    ) -> tuple[str | None, bytes | None, str | None]:
        if not self.capture_event_artifacts:
            return None, None, None

        html: str | None = None
        screenshot: bytes | None = None
        errors: list[str] = []

        try:
            html = self.page.content()
        except Exception as error:
            errors.append(f"html: {error}")

        try:
            screenshot = self.page.screenshot(
                full_page=True,
                timeout=self.artifact_timeout_ms,
            )
        except Exception as error:
            errors.append(f"screenshot: {error}")

        return html, screenshot, "; ".join(errors) or None

    def _restoration_error_result(
        self,
        candidate: EventCandidate,
        source_state: UIState,
        restore_result: RestoreResult,
    ) -> UIEventResult:
        return UIEventResult(
            event=candidate.to_ui_event(),
            candidate=candidate.to_dict(),
            changed=False,
            before_fingerprint=source_state.structural_signature,
            after_fingerprint=source_state.structural_signature,
            before_exact_fingerprint=source_state.exact_signature,
            after_exact_fingerprint=source_state.exact_signature,
            before_route=source_state.route,
            after_route=source_state.route,
            after_screen_data=restore_result.screen_data,
            error="state_restore_failed",
            source_state_id=source_state.state_id,
            restored_before=False,
            restore_strategy=restore_result.strategy,
            restore_error=restore_result.error,
        )

    def _error_result(
        self,
        candidate: EventCandidate,
        before_fingerprint: str,
        before_exact_fingerprint: str,
        before_route: str,
        before_screen_data: dict[str, Any],
        error: str,
        source_state_id: str | None = None,
        restore_result: RestoreResult | None = None,
    ) -> UIEventResult:
        return UIEventResult(
            event=candidate.to_ui_event(),
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
            source_state_id=source_state_id,
            restored_before=restore_result is not None,
            restore_strategy=(restore_result.strategy if restore_result else None),
        )
