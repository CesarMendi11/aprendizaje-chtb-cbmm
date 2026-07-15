from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.browser.navigator import ERPNavigator
from src.crawler.path_replayer import PathReplayer, ReplayResult
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
        }


class StateRestorer:
    """
    Devuelve el navegador a un ``UIState`` conocido y verifica su firma.

    Estrategias:
    1. No hacer nada si ya se encuentra en el estado objetivo.
    2. Navegar directamente para estados raíz.
    3. Reproducir la trayectoria para estados dinámicos.
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

        current = self._observe_current_state()
        if current and self._matches(current[1], target):
            return RestoreResult(
                success=True,
                state_id=target.state_id,
                strategy="already_current",
                attempts=0,
                screen_data=current[0],
                signature=current[1],
            )

        last_error: str | None = None
        last_screen: dict[str, Any] = current[0] if current else {}
        last_signature: StateSignature | None = current[1] if current else None
        last_replay: ReplayResult | None = None

        for attempt in range(1, self.max_attempts + 1):
            if target.path and target.path.depth > 0:
                replay = self.path_replayer.replay(
                    target.path,
                    expected_target_state_id=target.state_id,
                )
                last_replay = replay
                last_screen = replay.screen_data
                last_signature = replay.signature
                if replay.success:
                    return RestoreResult(
                        success=True,
                        state_id=target.state_id,
                        strategy="path_replay",
                        attempts=attempt,
                        screen_data=replay.screen_data,
                        signature=replay.signature,
                        replay=replay,
                    )
                last_error = replay.error
                continue

            direct = self._restore_root_state(target)
            last_screen = direct.screen_data
            last_signature = direct.signature
            last_error = direct.error
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
        )

    def _restore_root_state(self, target: UIState) -> RestoreResult:
        try:
            self.navigator.goto_path(target.route)
            if self.page_wait_ms > 0:
                self.navigator.page.wait_for_timeout(self.page_wait_ms)
            screen_data = self.extractor.extract()
            signature = self.signature_builder.build(screen_data)
            if not self._matches(signature, target):
                return RestoreResult(
                    success=False,
                    state_id=target.state_id,
                    strategy="direct_route",
                    attempts=1,
                    screen_data=screen_data,
                    signature=signature,
                    error=(
                        "La navegación directa llegó a una firma estructural "
                        "diferente de la esperada."
                    ),
                )
            return RestoreResult(
                success=True,
                state_id=target.state_id,
                strategy="direct_route",
                attempts=1,
                screen_data=screen_data,
                signature=signature,
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
            )

    def _observe_current_state(
        self,
    ) -> tuple[dict[str, Any], StateSignature] | None:
        try:
            screen_data = self.extractor.extract()
            signature = self.signature_builder.build(screen_data)
            return screen_data, signature
        except Exception:
            return None

    @staticmethod
    def _matches(signature: StateSignature, state: UIState) -> bool:
        return signature.structural_fingerprint == state.structural_signature
