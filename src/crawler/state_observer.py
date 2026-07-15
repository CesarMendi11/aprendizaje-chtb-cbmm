from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.crawler.state_signature import StateSignature, StateSignatureBuilder
from src.extraction.screen_extractor import ScreenExtractor


@dataclass(frozen=True)
class StateObservation:
    """Muestreo estable de una pantalla antes de registrar o comparar estados."""

    screen_data: dict[str, Any]
    signature: StateSignature
    stable: bool
    samples_count: int
    consecutive_samples: int
    observed_fingerprints: tuple[str, ...]
    elapsed_ms: int

    def diagnostics(self) -> dict[str, Any]:
        return {
            "stable": self.stable,
            "samples_count": self.samples_count,
            "consecutive_samples": self.consecutive_samples,
            "observed_fingerprints": list(self.observed_fingerprints),
            "elapsed_ms": self.elapsed_ms,
            "final_fingerprint": self.signature.structural_fingerprint,
            "final_exact_fingerprint": self.signature.exact_fingerprint,
            "final_title": self.signature.title,
            "final_route": self.signature.route,
        }


class StableStateObserver:
    """Espera una firma estructural estable mediante muestreo consecutivo.

    Muchos ERP cargan tablas, paginadores y componentes Angular después de que
    la navegación ya terminó. Una sola extracción puede registrar un estado
    parcial que luego resulta imposible de restaurar. Este observador exige dos
    o más muestras estructurales consecutivas antes de considerar estable una
    pantalla. La función puede desactivarse por perfil para pruebas rápidas.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        extractor: ScreenExtractor,
        signature_builder: StateSignatureBuilder,
        wait_fn: Callable[[int], None] | None = None,
    ):
        config = profile.get("state_detection", {}).get("stability", {})
        self.extractor = extractor
        self.signature_builder = signature_builder
        self.wait_fn = wait_fn
        self.enabled = bool(config.get("enabled", False))
        self.timeout_ms = max(0, int(config.get("timeout_ms", 3000)))
        self.interval_ms = max(0, int(config.get("interval_ms", 250)))
        self.minimum_observation_ms = max(
            0, int(config.get("minimum_observation_ms", 0))
        )
        self.required_consecutive_samples = max(
            1,
            int(config.get("required_consecutive_samples", 2)),
        )

    def observe(
        self,
        title_hint: str = "",
        canonical_title: str | None = None,
    ) -> StateObservation:
        fingerprints: list[str] = []
        samples_count = 0
        consecutive = 0
        previous_fingerprint: str | None = None
        last_screen_data: dict[str, Any] | None = None
        last_signature: StateSignature | None = None
        elapsed_ms = 0

        while True:
            screen_data = self._extract(title_hint=title_hint)
            self._apply_canonical_title(screen_data, canonical_title)
            signature = self.signature_builder.build(screen_data)

            samples_count += 1
            fingerprints.append(signature.structural_fingerprint)
            last_screen_data = screen_data
            last_signature = signature

            if signature.structural_fingerprint == previous_fingerprint:
                consecutive += 1
            else:
                previous_fingerprint = signature.structural_fingerprint
                consecutive = 1

            if not self.enabled:
                break
            if (
                consecutive >= self.required_consecutive_samples
                and elapsed_ms >= self.minimum_observation_ms
            ):
                break
            if elapsed_ms >= self.timeout_ms:
                break
            if self.interval_ms <= 0:
                break

            self._wait(self.interval_ms)
            elapsed_ms += self.interval_ms

        if last_screen_data is None or last_signature is None:
            raise RuntimeError("No se pudo observar ninguna muestra de pantalla.")

        stable = (
            not self.enabled
            or consecutive >= self.required_consecutive_samples
        )
        observation = StateObservation(
            screen_data=last_screen_data,
            signature=last_signature,
            stable=stable,
            samples_count=samples_count,
            consecutive_samples=consecutive,
            observed_fingerprints=tuple(fingerprints),
            elapsed_ms=elapsed_ms,
        )
        last_screen_data["state_observation"] = observation.diagnostics()
        return observation

    def _extract(self, title_hint: str) -> dict[str, Any]:
        try:
            return self.extractor.extract(title_hint=title_hint)
        except TypeError as error:
            if "title_hint" not in str(error):
                raise
            return self.extractor.extract()

    @staticmethod
    def _apply_canonical_title(
        screen_data: dict[str, Any],
        canonical_title: str | None,
    ) -> None:
        if not canonical_title:
            return

        screen_data["observed_functional_title"] = (
            screen_data.get("functional_title")
            or screen_data.get("title")
            or ""
        )
        screen_data["observed_title_source"] = screen_data.get(
            "title_source", ""
        )
        screen_data["functional_title"] = canonical_title
        screen_data["title_source"] = "state_registry"
        screen_data["title_confidence"] = 1.0

    def _wait(self, milliseconds: int) -> None:
        if milliseconds > 0 and self.wait_fn is not None:
            self.wait_fn(milliseconds)
