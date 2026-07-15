from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


@dataclass(frozen=True)
class InteractionResult:
    """Resultado de una interacción controlada con un elemento de la UI."""

    success: bool
    selector: str
    attempts: int
    strategy: str
    error: str | None = None


class BrowserInteractionExecutor:
    """
    Ejecuta clics reproducibles sin usar ``force=True``.

    Los ERP suelen animar menús, reconstruir componentes o desplazar el layout
    después de una navegación. Un único timeout corto puede producir falsos
    negativos aunque el selector sea correcto. Este ejecutor espera que el
    elemento sea visible, lo desplaza al viewport y reintenta de forma acotada.

    La seguridad de negocio no se decide aquí. El llamador debe entregar solo
    eventos previamente autorizados por ``EventPolicy``.
    """

    def __init__(
        self,
        page: Page,
        profile: dict,
        default_timeout_ms: int = 2500,
    ):
        self.page = page
        config = profile.get("browser_interaction", {}) or {}

        self.click_timeout_ms = max(
            250,
            int(config.get("click_timeout_ms", default_timeout_ms)),
        )
        self.click_attempts = max(1, int(config.get("click_attempts", 1)))
        self.retry_wait_ms = max(0, int(config.get("retry_wait_ms", 400)))
        self.pre_click_wait_ms = max(
            0,
            int(config.get("pre_click_wait_ms", 150)),
        )
        self.scroll_into_view = bool(config.get("scroll_into_view", True))

    def click(
        self,
        selector: str,
        *,
        timeout_ms: int | None = None,
    ) -> InteractionResult:
        if not selector:
            return InteractionResult(
                success=False,
                selector=selector,
                attempts=0,
                strategy="validated_click",
                error="El selector está vacío.",
            )

        timeout = max(250, int(timeout_ms or self.click_timeout_ms))
        errors: list[str] = []

        for attempt in range(1, self.click_attempts + 1):
            try:
                locator = self.page.locator(selector).first
                locator.wait_for(state="attached", timeout=timeout)
                locator.wait_for(state="visible", timeout=timeout)

                if self.scroll_into_view:
                    locator.scroll_into_view_if_needed(timeout=timeout)

                if self.pre_click_wait_ms:
                    self.page.wait_for_timeout(self.pre_click_wait_ms)

                locator.click(timeout=timeout)
                return InteractionResult(
                    success=True,
                    selector=selector,
                    attempts=attempt,
                    strategy="validated_click",
                )
            except PlaywrightTimeoutError as error:
                errors.append(f"attempt_{attempt}: timeout: {error}")
            except Exception as error:  # frontera defensiva del navegador
                errors.append(f"attempt_{attempt}: {error}")

            if attempt < self.click_attempts and self.retry_wait_ms:
                self.page.wait_for_timeout(self.retry_wait_ms)

        return InteractionResult(
            success=False,
            selector=selector,
            attempts=self.click_attempts,
            strategy="validated_click",
            error=" | ".join(errors) or "No se pudo ejecutar el clic.",
        )
