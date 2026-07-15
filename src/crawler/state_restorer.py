from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.browser.navigator import ERPNavigator
from src.crawler.path_replayer import PathReplayer, ReplayResult
from src.crawler.state_observer import StableStateObserver, StateObservation
from src.crawler.state_registry import StateRegistry
from src.crawler.state_signature import StateSignature, StateSignatureBuilder
from src.extraction.screen_extractor import ScreenExtractor
from src.models.ui_state import UIState


@dataclass
class RestoreResult:
    """Resultado de restaurar un estado fuente antes de explorar un evento."""

    success: bool
    state_id: str
    strategy: str
    attempts: int
    screen_data: dict[str, Any]
    signature: StateSignature | None
    error: str | None = None
    replay: ReplayResult | None = None
    match_mode: str | None = None
    observation: dict[str, Any] = field(default_factory=dict)
    expected_fingerprint: str | None = None
    observed_fingerprint: str | None = None
    expected_title: str | None = None
    observed_title: str | None = None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "strategy": self.strategy,
            "attempts": self.attempts,
            "match_mode": self.match_mode,
            "error": self.error,
            "expected_fingerprint": self.expected_fingerprint,
            "observed_fingerprint": self.observed_fingerprint,
            "expected_title": self.expected_title,
            "observed_title": self.observed_title,
            "observation": self.observation,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "state_id": self.state_id,
            "strategy": self.strategy,
            "attempts": self.attempts,
            "screen_data": self.screen_data,
            "signature": self.signature.to_dict() if self.signature else None,
            "error": self.error,
            "replay": self.replay.to_dict() if self.replay else None,
            "match_mode": self.match_mode,
            "observation": self.observation,
            "expected_fingerprint": self.expected_fingerprint,
            "observed_fingerprint": self.observed_fingerprint,
            "expected_title": self.expected_title,
            "observed_title": self.observed_title,
        }


class StateRestorer:
    """
    Devuelve el navegador a un ``UIState`` conocido y verifica su firma.

    Estrategias:
    1. No hacer nada si ya se encuentra en el estado objetivo.
    2. Navegar directamente para estados raíz.
    3. Reproducir la trayectoria para estados dinámicos.

    Las rutas raíz se vuelven a observar hasta estabilizarse y reutilizan el
    título canónico registrado. Esto evita falsos negativos por títulos SPA
    distintos o por tablas que terminan de cargar después de la navegación.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        navigator: ERPNavigator,
        extractor: ScreenExtractor,
        signature_builder: StateSignatureBuilder,
        registry: StateRegistry,
        path_replayer: PathReplayer,
    ):
        self.profile = profile
        self.navigator = navigator
        self.extractor = extractor
        self.signature_builder = signature_builder
        self.registry = registry
        self.path_replayer = path_replayer

        config = profile.get("state_replay", {})
        exploration = profile.get("exploration", {})
        self.page_wait_ms = int(
            config.get("page_wait_ms", exploration.get("page_wait_ms", 800))
        )
        self.max_attempts = max(1, int(config.get("restore_attempts", 2)))

    def restore(self, state: UIState | str) -> RestoreResult:
        target = self.registry.require(state) if isinstance(state, str) else state

        current = self._observe_current_state(target)
        if current and current.stable and self._matches(current.signature, target):
            return self._success_result(
                target=target,
                strategy="already_current",
                attempts=0,
                observation=current,
                match_mode="structural_fingerprint",
            )

        last_error: str | None = None
        last_screen: dict[str, Any] = current.screen_data if current else {}
        last_signature: StateSignature | None = current.signature if current else None
        last_replay: ReplayResult | None = None
        last_observation: dict[str, Any] = (
            current.diagnostics() if current else {}
        )

        for attempt in range(1, self.max_attempts + 1):
            if target.path and target.path.depth > 0:
                replay = self.path_replayer.replay(
                    target.path,
                    expected_target_state_id=target.state_id,
                )
                last_replay = replay
                last_screen = replay.screen_data
                last_signature = replay.signature
                last_error = replay.error
                last_observation = replay.observation
                if replay.success:
                    return RestoreResult(
                        success=True,
                        state_id=target.state_id,
                        strategy="path_replay",
                        attempts=attempt,
                        screen_data=replay.screen_data,
                        signature=replay.signature,
                        replay=replay,
                        match_mode="structural_fingerprint",
                        observation=replay.observation,
                        expected_fingerprint=target.structural_signature,
                        observed_fingerprint=(
                            replay.signature.structural_fingerprint
                            if replay.signature
                            else None
                        ),
                        expected_title=target.title,
                        observed_title=(
                            replay.signature.title if replay.signature else None
                        ),
                    )
                continue

            direct = self._restore_root_state(target)
            last_screen = direct.screen_data
            last_signature = direct.signature
            last_error = direct.error
            last_observation = direct.observation
            if direct.success:
                direct.attempts = attempt
                return direct

        return RestoreResult(
            success=False,
            state_id=target.state_id,
            strategy="failed",
            attempts=self.max_attempts,
            screen_data=last_screen,
            signature=last_signature,
            error=last_error or "No se pudo restaurar el estado.",
            replay=last_replay,
            match_mode=None,
            observation=last_observation,
            expected_fingerprint=target.structural_signature,
            observed_fingerprint=(
                last_signature.structural_fingerprint if last_signature else None
            ),
            expected_title=target.title,
            observed_title=(last_signature.title if last_signature else None),
        )

    def _restore_root_state(self, target: UIState) -> RestoreResult:
        try:
            self.navigator.goto_path(target.route)
            if self.page_wait_ms > 0:
                self.navigator.page.wait_for_timeout(self.page_wait_ms)

            observation = self._observe_target_root(target)
            if not observation.stable or not self._matches(observation.signature, target):
                return RestoreResult(
                    success=False,
                    state_id=target.state_id,
                    strategy="direct_route",
                    attempts=1,
                    screen_data=observation.screen_data,
                    signature=observation.signature,
                    error=(
                        "La observación directa no alcanzó estabilidad."
                        if not observation.stable
                        else (
                            "La navegación directa llegó a una firma estructural "
                            "diferente de la esperada."
                        )
                    ),
                    observation=observation.diagnostics(),
                    expected_fingerprint=target.structural_signature,
                    observed_fingerprint=(
                        observation.signature.structural_fingerprint
                    ),
                    expected_title=target.title,
                    observed_title=observation.signature.title,
                )

            return self._success_result(
                target=target,
                strategy="direct_route",
                attempts=1,
                observation=observation,
                match_mode="structural_fingerprint",
            )
        except Exception as error:
            return RestoreResult(
                success=False,
                state_id=target.state_id,
                strategy="direct_route",
                attempts=1,
                screen_data={},
                signature=None,
                error=str(error),
                expected_fingerprint=target.structural_signature,
                expected_title=target.title,
            )

    def _observe_current_state(self, target: UIState) -> StateObservation | None:
        try:
            observer = self._observer()
            return observer.observe(
                title_hint=self._title_hint(target),
                canonical_title=target.title,
            )
        except Exception:
            return None

    def _observe_target_root(self, target: UIState) -> StateObservation:
        observer = self._observer()
        return observer.observe(
            title_hint=self._title_hint(target),
            canonical_title=target.title,
        )

    def _observer(self) -> StableStateObserver:
        return StableStateObserver(
            profile=self.profile,
            extractor=self.extractor,
            signature_builder=self.signature_builder,
            wait_fn=self.navigator.page.wait_for_timeout,
        )

    @staticmethod
    def _title_hint(target: UIState) -> str:
        return str(
            target.metadata.get("title_hint")
            or target.metadata.get("canonical_title")
            or target.title
            or ""
        )

    @staticmethod
    def _matches(signature: StateSignature, state: UIState) -> bool:
        return signature.structural_fingerprint == state.structural_signature

    @staticmethod
    def _success_result(
        target: UIState,
        strategy: str,
        attempts: int,
        observation: StateObservation,
        match_mode: str,
    ) -> RestoreResult:
        return RestoreResult(
            success=True,
            state_id=target.state_id,
            strategy=strategy,
            attempts=attempts,
            screen_data=observation.screen_data,
            signature=observation.signature,
            match_mode=match_mode,
            observation=observation.diagnostics(),
            expected_fingerprint=target.structural_signature,
            observed_fingerprint=observation.signature.structural_fingerprint,
            expected_title=target.title,
            observed_title=observation.signature.title,
        )
