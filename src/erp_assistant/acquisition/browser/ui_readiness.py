from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page


@dataclass(frozen=True)
class UIReadinessResult:
    """Resultado de esperar que desaparezcan bloqueadores transitorios."""

    ready: bool
    waited_ms: int
    checks: int
    active_selectors: tuple[str, ...] = ()
    saw_blocker: bool = False

    def diagnostics(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "waited_ms": self.waited_ms,
            "checks": self.checks,
            "active_selectors": list(self.active_selectors),
            "saw_blocker": self.saw_blocker,
        }


class UIReadinessWaiter:
    """Espera overlays transitorios que vuelven la UI no interactuable.

    No usa ``networkidle`` porque un ERP puede mantener polling u otras
    solicitudes en segundo plano. La condición se define desde el perfil con
    selectores de superficies que el propio frontend usa para bloquear la UI
    mientras procesa una operación. Si nunca se observa un bloqueador, la
    comprobación retorna inmediatamente; el período de estabilidad sólo se
    aplica después de haber visto uno.
    """

    def __init__(self, page: Page, profile: dict[str, Any]):
        self.page = page
        config = profile.get("ui_readiness", {}) or {}

        self.enabled = bool(config.get("enabled", False))
        self.blocking_selectors = tuple(
            str(selector).strip()
            for selector in config.get("blocking_selectors", [])
            if str(selector).strip()
        )
        self.timeout_ms = max(0, int(config.get("timeout_ms", 15000)))
        self.poll_interval_ms = max(1, int(config.get("poll_interval_ms", 100)))
        self.clear_stability_ms = max(
            0,
            int(config.get("clear_stability_ms", 250)),
        )

    def wait_until_ready(self) -> UIReadinessResult:
        if not self.enabled or not self.blocking_selectors:
            return UIReadinessResult(
                ready=True,
                waited_ms=0,
                checks=0,
            )

        waited_ms = 0
        checks = 0
        clear_elapsed_ms = 0
        saw_blocker = False
        last_active: tuple[str, ...] = ()

        while True:
            active = self._active_blockers()
            checks += 1

            if active:
                saw_blocker = True
                clear_elapsed_ms = 0
                last_active = active
            elif not saw_blocker or clear_elapsed_ms >= self.clear_stability_ms:
                return UIReadinessResult(
                    ready=True,
                    waited_ms=waited_ms,
                    checks=checks,
                    active_selectors=(),
                    saw_blocker=saw_blocker,
                )

            if waited_ms >= self.timeout_ms:
                return UIReadinessResult(
                    ready=False,
                    waited_ms=waited_ms,
                    checks=checks,
                    active_selectors=last_active,
                    saw_blocker=saw_blocker,
                )

            step_ms = min(
                self.poll_interval_ms,
                max(1, self.timeout_ms - waited_ms),
            )
            self.page.wait_for_timeout(step_ms)
            waited_ms += step_ms

            if saw_blocker and not active:
                clear_elapsed_ms += step_ms

    def _active_blockers(self) -> tuple[str, ...]:
        active: list[str] = []
        for selector in self.blocking_selectors:
            if self.page.locator(selector).first.is_visible():
                active.append(selector)
        return tuple(active)
