from erp_assistant.acquisition.crawling.state_observer import StableStateObserver
from erp_assistant.acquisition.crawling.state_signature import StateSignatureBuilder


def screen(text: str, title: str = "Facturas") -> dict:
    return {
        "path": "/admin/facturas",
        "title": "Dashboard",
        "functional_title": title,
        "visible_text": text,
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
        "dialogs": [],
    }


class SequenceExtractor:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def extract(self, title_hint: str = ""):
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return dict(self.values[index])


def profile(required_samples: int = 2, timeout_ms: int = 1000) -> dict:
    return {
        "state_detection": {
            "stability": {
                "enabled": True,
                "timeout_ms": timeout_ms,
                "interval_ms": 10,
                "required_consecutive_samples": required_samples,
            }
        }
    }


def test_state_observer_waits_for_two_consecutive_structural_signatures():
    extractor = SequenceExtractor(
        [
            screen("Cargando"),
            screen("Tabla lista"),
            screen("Tabla lista"),
        ]
    )
    waits = []
    observer = StableStateObserver(
        profile=profile(),
        extractor=extractor,
        signature_builder=StateSignatureBuilder(),
        wait_fn=waits.append,
    )

    result = observer.observe()

    assert result.stable is True
    assert result.samples_count == 3
    assert result.consecutive_samples == 2
    assert result.screen_data["visible_text"] == "Tabla lista"
    assert waits == [10, 10]


def test_state_observer_applies_registered_canonical_title():
    extractor = SequenceExtractor([screen("Contenido", title="Facturacion")])
    observer = StableStateObserver(
        profile={"state_detection": {"stability": {"enabled": False}}},
        extractor=extractor,
        signature_builder=StateSignatureBuilder(),
    )

    result = observer.observe(
        title_hint="Facturación Electronica",
        canonical_title="Facturación Electronica",
    )

    assert result.signature.title == "Facturación Electronica"
    assert result.screen_data["observed_functional_title"] == "Facturacion"
    assert result.screen_data["title_source"] == "state_registry"
    assert result.stable is True


def test_state_observer_reports_unstable_when_timeout_expires():
    extractor = SequenceExtractor([screen("Uno"), screen("Dos"), screen("Tres"), screen("Cuatro")])
    observer = StableStateObserver(
        profile=profile(required_samples=2, timeout_ms=20),
        extractor=extractor,
        signature_builder=StateSignatureBuilder(),
        wait_fn=lambda _: None,
    )

    result = observer.observe()

    assert result.stable is False
    assert result.samples_count == 3
    assert result.consecutive_samples == 1


def test_state_observer_respects_minimum_observation_time():
    extractor = SequenceExtractor([screen("Contenido estable")])
    cfg = profile(required_samples=2, timeout_ms=100)
    cfg["state_detection"]["stability"]["minimum_observation_ms"] = 20
    waits = []
    observer = StableStateObserver(
        profile=cfg,
        extractor=extractor,
        signature_builder=StateSignatureBuilder(),
        wait_fn=waits.append,
    )

    result = observer.observe()

    assert result.stable is True
    assert result.samples_count == 3
    assert result.elapsed_ms == 20
    assert waits == [10, 10]


class ReadinessLocator:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.page.visibility[min(self.page.index, len(self.page.visibility) - 1)]


class ReadinessPage:
    def __init__(self, visibility):
        self.visibility = list(visibility)
        self.index = 0
        self.waits = []

    def locator(self, selector):
        assert selector == ".loader"
        return ReadinessLocator(self)

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)
        self.index += 1


def test_state_observer_waits_for_profile_blocker_before_extracting():
    extractor = SequenceExtractor([screen("Contenido listo")])
    page = ReadinessPage([True, False, False])
    cfg = {
        "state_detection": {"stability": {"enabled": False}},
        "ui_readiness": {
            "enabled": True,
            "blocking_selectors": [".loader"],
            "timeout_ms": 1000,
            "poll_interval_ms": 50,
            "clear_stability_ms": 50,
        },
    }
    observer = StableStateObserver(
        profile=cfg,
        extractor=extractor,
        signature_builder=StateSignatureBuilder(),
        wait_fn=page.wait_for_timeout,
        page=page,
    )

    result = observer.observe()

    assert result.stable is True
    assert extractor.calls == 1
    assert page.waits == [50, 50]
