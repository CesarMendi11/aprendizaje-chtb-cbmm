from erp_assistant.acquisition.extraction.screen_title_resolver import ScreenTitleResolver


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
            "title_candidates": [{"text": "Bienvenido", "source": "main_heading", "score": 100}],
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


def test_in_screen_title_uses_only_in_screen_heading_sources():
    resolver = ScreenTitleResolver(profile())
    screen_data = {
        "path": "/admin/cuentas/forma-pago",
        "title_candidates": [
            {"text": "Tipo pago", "source": "visual_heading", "score": 91},
            {"text": "Forma pago", "source": "active_navigation", "score": 78},
        ],
    }

    observed = resolver.resolve_in_screen(screen_data)
    functional = resolver.resolve(screen_data, title_hint="Forma pago")

    assert observed.title == "Tipo pago"
    assert observed.source == "visual_heading"
    assert functional.title == "Tipo pago"
    assert functional.source == "visual_heading"


def test_in_screen_title_is_empty_when_only_navigation_or_discovery_name_exists():
    resolver = ScreenTitleResolver(profile())
    screen_data = {
        "path": "/admin/cuentas/facturacion",
        "title_candidates": [
            {"text": "Facturación", "source": "active_navigation", "score": 78},
        ],
    }

    observed = resolver.resolve_in_screen(screen_data)
    functional = resolver.resolve(screen_data, title_hint="Facturación")

    assert observed.title == ""
    assert observed.source == "not_observed"
    assert functional.title == "Facturación"
    assert functional.source == "discovery_hint"


def test_route_override_does_not_fabricate_an_in_screen_heading():
    resolver = ScreenTitleResolver(profile())
    screen_data = {"path": "/admin/home", "title_candidates": []}

    observed = resolver.resolve_in_screen(screen_data)
    functional = resolver.resolve(screen_data)

    assert observed.title == ""
    assert functional.title == "Dashboard principal"
    assert functional.source == "route_override"


def test_facturacion_like_local_labels_do_not_become_screen_title():
    resolver = ScreenTitleResolver(profile())
    screen_data = {
        "path": "/admin/cuentas/facturacion",
        "title_candidates": [
            {"text": "CAJA", "source": "visual_heading", "score": 88},
            {"text": "FACTURA No.", "source": "visual_heading", "score": 88},
            {"text": "1234567890", "source": "visual_heading", "score": 93},
        ],
    }

    observed = resolver.resolve_in_screen(screen_data)
    functional = resolver.resolve(screen_data, title_hint="Facturación Electronica")

    assert observed.title == ""
    assert observed.source == "not_observed"
    assert functional.title == "Facturación Electronica"
    assert functional.source == "discovery_hint"


def test_prominent_safe_visual_heading_remains_eligible():
    resolver = ScreenTitleResolver(profile())
    screen_data = {
        "path": "/admin/permisos/solicitudes",
        "title_candidates": [
            {"text": "Solicitudes Registrados", "source": "visual_heading", "score": 90},
        ],
    }

    observed = resolver.resolve_in_screen(screen_data)
    functional = resolver.resolve(screen_data, title_hint="Aprobar solicitudes")

    assert observed.title == "Solicitudes Registrados"
    assert observed.source == "visual_heading"
    assert functional.title == "Solicitudes Registrados"
    assert functional.source == "visual_heading"
