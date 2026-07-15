from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

from src.browser.interaction_executor import BrowserInteractionExecutor

from src.browser.navigator import ERPNavigator
from src.crawler.state_observer import StableStateObserver
from src.crawler.state_registry import StateRegistry
from src.crawler.state_signature import StateSignature, StateSignatureBuilder
from src.extraction.screen_extractor import ScreenExtractor
from src.models.crawl_path import CrawlPath, CrawlPathStep
from src.models.ui_event import EventDecision, UIEvent


@dataclass
class ReplayResult:
    """Resultado de reproducir una trayectoria de interfaz."""

    success: bool
    root_state_id: str
    expected_target_state_id: str | None
    reached_state_id: str | None
    completed_steps: int
    screen_data: dict[str, Any]
    signature: StateSignature | None
    error: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "root_state_id": self.root_state_id,
            "expected_target_state_id": self.expected_target_state_id,
            "reached_state_id": self.reached_state_id,
            "completed_steps": self.completed_steps,
            "screen_data": self.screen_data,
            "signature": self.signature.to_dict() if self.signature else None,
            "error": self.error,
            "observation": self.observation or {},
        }


class PathReplayer:
    """
    Reproduce de forma determinística una ``CrawlPath`` usando Playwright.

    Solo ejecuta eventos previamente clasificados como ``allow``. Cada paso se
    valida contra el estado esperado cuando el target ya está registrado.
    """

    def __init__(
        self,
        page: Page,
        profile: dict[str, Any],
        navigator: ERPNavigator,
        extractor: ScreenExtractor,
        signature_builder: StateSignatureBuilder,
        registry: StateRegistry,
    ):
        self.page = page
        self.profile = profile
        self.navigator = navigator
        self.extractor = extractor
        self.signature_builder = signature_builder
        self.registry = registry

        config = profile.get("state_replay", {})
        ui_events = profile.get("ui_events", {})
        exploration = profile.get("exploration", {})

        self.page_wait_ms = int(
            config.get("page_wait_ms", exploration.get("page_wait_ms", 800))
        )
        self.step_wait_ms = int(
            config.get("step_wait_ms", ui_events.get("event_wait_ms", 400))
        )
        self.click_timeout_ms = int(
            config.get(
                "click_timeout_ms",
                ui_events.get("click_timeout_ms", 2500),
            )
        )
        self.verify_each_step = bool(config.get("verify_each_step", True))
        self.interaction_executor = BrowserInteractionExecutor(
            page=page,
            profile=profile,
            default_timeout_ms=self.click_timeout_ms,
        )

    def replay(
        self,
        path: CrawlPath,
        expected_target_state_id: str | None = None,
    ) -> ReplayResult:
        screen_data: dict[str, Any] = {}
        signature: StateSignature | None = None
        completed_steps = 0
        observation_diagnostics: dict[str, Any] = {}

        try:
            root_state = self.registry.require(path.root_state_id)
            self.navigator.goto_path(root_state.route)
            self._wait(self.page_wait_ms)

            observation = self._observe(
                title_hint=self._state_title_hint(root_state),
                canonical_title=root_state.title,
            )
            observation_diagnostics = observation.diagnostics()
            self._assert_observation_stable(observation, "estado raíz")
            screen_data = observation.screen_data
            signature = observation.signature
            self._assert_matches_state(signature, root_state.state_id, "estado raíz")

            reached_state_id = root_state.state_id

            for step in path.steps:
                self._validate_step_source(step, reached_state_id)
                self._execute_event(step.event)
                self._wait(self.step_wait_ms)

                expected_state = (
                    self.registry.get(step.target_state_id)
                    if step.target_state_id
                    else None
                )
                observation = self._observe(
                    title_hint=(
                        self._state_title_hint(expected_state)
                        if expected_state
                        else root_state.title
                    ),
                    canonical_title=(
                        expected_state.title if expected_state else root_state.title
                    ),
                )
                observation_diagnostics = observation.diagnostics()
                self._assert_observation_stable(observation, "paso reproducido")
                screen_data = observation.screen_data
                signature = observation.signature
                completed_steps += 1

                reached_state_id = self._resolve_reached_state_id(
                    step=step,
                    signature=signature,
                )

            target_state_id = expected_target_state_id or path.target_state_id
            if target_state_id:
                self._assert_matches_state(signature, target_state_id, "estado objetivo")
                reached_state_id = target_state_id

            return ReplayResult(
                success=True,
                root_state_id=path.root_state_id,
                expected_target_state_id=target_state_id,
                reached_state_id=reached_state_id,
                completed_steps=completed_steps,
                screen_data=screen_data,
                signature=signature,
            )

        except (KeyError, ValueError, RuntimeError) as error:
            return ReplayResult(
                success=False,
                root_state_id=path.root_state_id,
                expected_target_state_id=expected_target_state_id,
                reached_state_id=None,
                completed_steps=completed_steps,
                screen_data=screen_data,
                signature=signature,
                error=str(error),
                observation=observation_diagnostics,
            )
        except Exception as error:  # frontera defensiva del navegador
            return ReplayResult(
                success=False,
                root_state_id=path.root_state_id,
                expected_target_state_id=expected_target_state_id,
                reached_state_id=None,
                completed_steps=completed_steps,
                screen_data=screen_data,
                signature=signature,
                error=f"unexpected_replay_error: {error}",
                observation=observation_diagnostics,
            )


    def _observe(
        self,
        title_hint: str = "",
        canonical_title: str | None = None,
    ):
        observer = StableStateObserver(
            profile=self.profile,
            extractor=self.extractor,
            signature_builder=self.signature_builder,
            wait_fn=self.page.wait_for_timeout,
        )
        return observer.observe(
            title_hint=title_hint,
            canonical_title=canonical_title,
        )

    @staticmethod
    def _state_title_hint(state) -> str:
        if state is None:
            return ""
        return str(
            state.metadata.get("title_hint")
            or state.metadata.get("canonical_title")
            or state.title
            or ""
        )


    @staticmethod
    def _assert_observation_stable(observation, context: str) -> None:
        if not observation.stable:
            raise RuntimeError(
                f"La observación de {context} no alcanzó una firma estable."
            )

    def _execute_event(self, event: UIEvent) -> None:
        if event.decision is not EventDecision.ALLOW:
            raise ValueError(
                "PathReplayer rechazó un evento no autorizado: "
                f"{event.label!r} ({event.decision.value})."
            )
        if not event.selector:
            raise ValueError("El evento no tiene selector reproducible.")

        interaction = self.interaction_executor.click(event.selector)
        if not interaction.success:
            raise RuntimeError(
                "No se pudo reproducir el evento "
                f"{event.label!r} después de {interaction.attempts} intentos: "
                f"{interaction.error}"
            )

    def _validate_step_source(
        self,
        step: CrawlPathStep,
        reached_state_id: str,
    ) -> None:
        if step.source_state_id != reached_state_id:
            raise RuntimeError(
                "Trayectoria inconsistente: el paso esperaba salir de "
                f"{step.source_state_id}, pero se alcanzó {reached_state_id}."
            )

    def _resolve_reached_state_id(
        self,
        step: CrawlPathStep,
        signature: StateSignature,
    ) -> str:
        if step.target_state_id:
            if self.verify_each_step:
                self._assert_matches_state(
                    signature,
                    step.target_state_id,
                    "paso reproducido",
                )
            return step.target_state_id

        registered = self.registry.find_by_signature(
            signature.structural_fingerprint
        )
        if registered:
            return registered.state_id
        return self.registry.build_state_id(signature.structural_fingerprint)

    def _assert_matches_state(
        self,
        signature: StateSignature | None,
        state_id: str,
        context: str,
    ) -> None:
        if signature is None:
            raise RuntimeError(f"No existe firma para validar {context}.")
        expected = self.registry.require(state_id)
        if signature.structural_fingerprint != expected.structural_signature:
            raise RuntimeError(
                f"No se pudo reproducir {context} {state_id}. "
                "La firma estructural observada no coincide con la esperada."
            )

    def _wait(self, milliseconds: int) -> None:
        if milliseconds > 0:
            self.page.wait_for_timeout(milliseconds)
