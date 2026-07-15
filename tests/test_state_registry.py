from src.crawler.state_registry import StateRegistry
from src.crawler.state_signature import StateSignatureBuilder
from src.models.crawl_path import CrawlPath, CrawlPathStep
from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType


def make_screen(text: str = "Dashboard", path: str = "/admin/home") -> dict:
    return {
        "path": path,
        "title": "ERP",
        "visible_text": text,
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
        "dialogs": [],
    }


def make_event(label: str = "Abrir menú") -> UIEvent:
    return UIEvent(
        event_type=UIEventType.EXPAND_MENU,
        label=label,
        selector="button.open-menu",
        decision=EventDecision.ALLOW,
        risk_level=RiskLevel.LOW,
    )


def test_state_registry_deduplicates_by_structural_signature():
    builder = StateSignatureBuilder()
    registry = StateRegistry()

    first_signature = builder.build(make_screen("Dashboard 2026-07-15"))
    second_signature = builder.build(make_screen("Dashboard 2026-07-16"))

    first = registry.register_signature(first_signature)
    second = registry.register_signature(second_signature)

    assert first.is_new is True
    assert second.is_new is False
    assert first.state.state_id == second.state.state_id
    assert registry.count() == 1
    assert registry.exact_signatures_for(first.state.state_id) == {
        first_signature.exact_fingerprint,
        second_signature.exact_fingerprint,
    }


def test_state_registry_keeps_shortest_reproducible_path():
    builder = StateSignatureBuilder()
    registry = StateRegistry()
    signature = builder.build(make_screen("Menú abierto"))
    state_id = registry.build_state_id(signature.structural_fingerprint)

    root = "ui_state:root"
    long_path = CrawlPath(root_state_id=root).append(
        CrawlPathStep(root, make_event("Paso 1"), "ui_state:middle")
    ).append(
        CrawlPathStep("ui_state:middle", make_event("Paso 2"), state_id)
    )
    short_path = CrawlPath(root_state_id=root).append(
        CrawlPathStep(root, make_event("Paso directo"), state_id)
    )

    registry.register_signature(signature, path=long_path)
    result = registry.register_signature(signature, path=short_path)

    assert result.is_new is False
    assert result.path_updated is True
    assert result.state.path == short_path


def test_state_registry_serializes_observed_exact_signatures():
    builder = StateSignatureBuilder()
    registry = StateRegistry()
    signature = builder.build(make_screen())

    registered = registry.register_signature(signature)
    payload = registry.to_dict()

    assert payload["summary"]["states_count"] == 1
    assert payload["states"][0]["state_id"] == registered.state.state_id
    assert payload["states"][0]["observed_exact_signatures"] == [
        signature.exact_fingerprint
    ]
