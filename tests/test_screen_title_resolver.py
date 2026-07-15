from src.extraction.screen_title_resolver import ScreenTitleResolver


def profile() -> dict:
    return {
        "extraction": {
            "title_resolution": {
                "generic_document_titles": ["Dashboard"],
                "route_titles": {"/admin/home": "Dashboard principal"},
            }
        }
    }


def test_title_resolver_prefers_route_override():
    resolver = ScreenTitleResolver(profile())
    result = resolver.resolve(
        {
            "path": "/admin/home",
            "title": "Dashboard",
            "title_candidates": [
                {"text": "Bienvenido", "source": "main_heading", "score": 100}
            ],
        }
    )

    assert result.title == "Dashboard principal"
    assert result.source == "route_override"
    assert result.confidence == 1.0


def test_title_resolver_uses_main_heading_over_generic_document_title():
    resolver = ScreenTitleResolver(profile())
    result = resolver.resolve(
        {
            "path": "/admin/cuentas/comprobantes",
            "title": "Dashboard",
            "title_candidates": [
                {"text": "Consulta de comprobantes", "source": "main_heading", "score": 100}
            ],
        },
        title_hint="Comprobantes",
    )

    assert result.title == "Consulta de comprobantes"
    assert result.source == "main_heading"


def test_title_resolver_uses_discovery_hint_when_document_title_is_generic():
    resolver = ScreenTitleResolver(profile())
    result = resolver.resolve(
        {
            "path": "/admin/cuentas/retenciones",
            "title": "Dashboard",
            "title_candidates": [],
        },
        title_hint="Retenciones",
    )

    assert result.title == "Retenciones"
    assert result.source == "discovery_hint"


def test_title_resolver_falls_back_to_route_segment():
    resolver = ScreenTitleResolver(profile())
    result = resolver.resolve(
        {
            "path": "/admin/cuentas/lista-facturas",
            "title": "Dashboard",
            "title_candidates": [],
        }
    )

    assert result.title == "Lista facturas"
    assert result.source == "route_fallback"
