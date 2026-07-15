from src.crawler.state_signature import StateSignatureBuilder


def test_state_signature_builder_creates_stable_fingerprint():
    builder = StateSignatureBuilder()

    screen_data = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "Panel principal",
        "links": [
            {
                "text": "Facturas",
                "href": "/admin/facturas",
                "tag": "a",
            }
        ],
        "buttons": [
            {
                "text": "Buscar",
                "type": None,
                "role": None,
                "tag": "button",
            }
        ],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
    }

    first = builder.build(screen_data)
    second = builder.build(screen_data)

    assert first.fingerprint == second.fingerprint
    assert first.route == "/admin/home"
    assert first.title == "Dashboard"


def test_state_signature_ignores_order_of_links():
    builder = StateSignatureBuilder()

    first_data = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "Panel principal",
        "links": [
            {"text": "Facturas", "href": "/admin/facturas", "tag": "a"},
            {"text": "Clientes", "href": "/admin/clientes", "tag": "a"},
        ],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
    }

    second_data = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "Panel principal",
        "links": [
            {"text": "Clientes", "href": "/admin/clientes", "tag": "a"},
            {"text": "Facturas", "href": "/admin/facturas", "tag": "a"},
        ],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
    }

    first = builder.build(first_data)
    second = builder.build(second_data)

    assert first.fingerprint == second.fingerprint


def test_state_signature_changes_when_new_menu_items_appear():
    builder = StateSignatureBuilder()

    closed_menu = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "Dashboard Cuentas por cobrar",
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [
            {
                "text": "Cuentas por cobrar",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "role": None,
                "aria_expanded": "false",
                "onclick": False,
            }
        ],
    }

    opened_menu = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "Dashboard Cuentas por cobrar Facturas Retenciones",
        "links": [
            {
                "text": "Facturas",
                "href": "/admin/facturas",
                "tag": "a",
            },
            {
                "text": "Retenciones",
                "href": "/admin/retenciones",
                "tag": "a",
            },
        ],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [
            {
                "text": "Cuentas por cobrar",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "role": None,
                "aria_expanded": "true",
                "onclick": False,
            },
            {
                "text": "Facturas",
                "tag": "fuse-vertical-navigation-basic-item",
                "role": None,
                "aria_expanded": None,
                "onclick": False,
            },
        ],
    }

    before = builder.build(closed_menu)
    after = builder.build(opened_menu)

    assert before.fingerprint != after.fingerprint
    assert builder.has_changed(before, after)


def test_state_signature_truncates_visible_text():
    builder = StateSignatureBuilder(visible_text_limit=10)

    screen_data = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "A" * 100,
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
    }

    signature = builder.build(screen_data)

    assert signature.summary["visible_text"] == "aaaaaaaaaa"


def test_state_signature_removes_duplicate_buttons():
    builder = StateSignatureBuilder()

    screen_data = {
        "path": "/admin/home",
        "title": "Dashboard",
        "visible_text": "Panel principal",
        "links": [],
        "buttons": [
            {
                "text": "Buscar",
                "type": None,
                "role": None,
                "tag": "button",
            },
            {
                "text": "Buscar",
                "type": None,
                "role": None,
                "tag": "button",
            },
        ],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
    }

    signature = builder.build(screen_data)

    assert len(signature.summary["buttons"]) == 1

def test_state_signature_separates_exact_and_structural_changes():
    builder = StateSignatureBuilder()

    first_data = {
        "path": "/admin/facturas?id=123456",
        "title": "Facturas",
        "visible_text": "Actualizado 2026-07-15 10:30:00 Factura 123456",
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [{"headers": ["Número", "Estado"], "rows_count": 3}],
        "custom_interactives": [],
    }
    second_data = {
        "path": "/admin/facturas?id=987654",
        "title": "Facturas",
        "visible_text": "Actualizado 2026-07-16 11:45:00 Factura 987654",
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [{"headers": ["Número", "Estado"], "rows_count": 8}],
        "custom_interactives": [],
    }

    first = builder.build(first_data)
    second = builder.build(second_data)

    assert first.exact_fingerprint != second.exact_fingerprint
    assert first.structural_fingerprint == second.structural_fingerprint
    assert not builder.has_changed(first, second)
    assert builder.has_changed(first, second, mode="exact")


def test_state_signature_detects_active_tab_change():
    builder = StateSignatureBuilder()

    inactive = {
        "path": "/admin/facturas",
        "title": "Facturas",
        "visible_text": "Facturas Pendientes Emitidas",
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "custom_interactives": [
            {
                "text": "Pendientes",
                "tag": "button",
                "role": "tab",
                "aria_selected": "true",
            },
            {
                "text": "Emitidas",
                "tag": "button",
                "role": "tab",
                "aria_selected": "false",
            },
        ],
    }
    active = {
        **inactive,
        "custom_interactives": [
            {
                "text": "Pendientes",
                "tag": "button",
                "role": "tab",
                "aria_selected": "false",
            },
            {
                "text": "Emitidas",
                "tag": "button",
                "role": "tab",
                "aria_selected": "true",
            },
        ],
    }

    before = builder.build(inactive)
    after = builder.build(active)

    assert builder.has_changed(before, after)


def test_structural_signature_ignores_header_and_volatile_region_changes():
    builder = StateSignatureBuilder()

    base = {
        "path": "/admin/facturas",
        "title": "Dashboard",
        "functional_title": "Facturas",
        "visible_text": "Usuario Ana Facturas Historial 10:00 10.0.0.1",
        "main_visible_text": "Facturas Consulta de facturas",
        "regions": {
            "main_content": {"visible_text": "Facturas Consulta de facturas", "elements_count": 1},
            "dialog": {"visible_text": "", "elements_count": 0},
            "header": {"visible_text": "Usuario Ana", "elements_count": 1},
            "volatile": {"visible_text": "Historial 10:00 10.0.0.1", "elements_count": 1},
        },
        "links": [],
        "buttons": [
            {"text": "Buscar", "tag": "button", "region": "main_content"}
        ],
        "inputs": [],
        "tables": [],
        "custom_interactives": [],
        "dialogs": [],
    }
    changed_noise = {
        **base,
        "visible_text": "Usuario Luis Facturas Historial 11:00 192.168.1.8",
        "regions": {
            **base["regions"],
            "header": {"visible_text": "Usuario Luis", "elements_count": 1},
            "volatile": {"visible_text": "Historial 11:00 192.168.1.8", "elements_count": 4},
        },
    }

    first = builder.build(base)
    second = builder.build(changed_noise)

    assert first.exact_fingerprint != second.exact_fingerprint
    assert first.structural_fingerprint == second.structural_fingerprint


def test_structural_signature_keeps_global_navigation_expansion_state():
    builder = StateSignatureBuilder()

    closed = {
        "path": "/admin/home",
        "functional_title": "Dashboard",
        "visible_text": "Dashboard Cuentas por cobrar",
        "main_visible_text": "Dashboard",
        "regions": {
            "main_content": {"visible_text": "Dashboard", "elements_count": 0},
            "dialog": {"visible_text": "", "elements_count": 0},
        },
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "dialogs": [],
        "custom_interactives": [
            {
                "text": "Cuentas por cobrar",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "aria_expanded": "false",
                "region": "global_navigation",
            }
        ],
    }
    opened = {
        **closed,
        "links": [
            {
                "text": "Retenciones",
                "href": "/admin/retenciones",
                "tag": "a",
                "region": "global_navigation",
            }
        ],
        "custom_interactives": [
            {
                "text": "Cuentas por cobrar",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "aria_expanded": "true",
                "region": "global_navigation",
            }
        ],
    }

    assert builder.has_changed(builder.build(closed), builder.build(opened))


def test_profile_builder_uses_navigation_state_only_on_configured_routes():
    profile = {
        "navigation": {"home_url": "/admin/home"},
        "state_detection": {"navigation_state_routes": ["/admin/home"]},
    }
    builder = StateSignatureBuilder.from_profile(profile)

    def payload(path: str, opened: bool):
        return {
            "path": path,
            "functional_title": "Pantalla",
            "visible_text": "Pantalla",
            "main_visible_text": "Pantalla",
            "regions": {
                "main_content": {"visible_text": "Pantalla", "elements_count": 1},
                "dialog": {"visible_text": "", "elements_count": 0},
            },
            "links": (
                [
                    {
                        "text": "Retenciones",
                        "href": "/admin/retenciones",
                        "tag": "a",
                        "region": "global_navigation",
                    }
                ]
                if opened
                else []
            ),
            "buttons": [],
            "inputs": [],
            "tables": [],
            "dialogs": [],
            "custom_interactives": [
                {
                    "text": "Cuentas por cobrar",
                    "tag": "fuse-vertical-navigation-collapsable-item",
                    "aria_expanded": "true" if opened else "false",
                    "region": "global_navigation",
                }
            ],
        }

    internal_closed = builder.build(payload("/admin/facturas", False))
    internal_opened = builder.build(payload("/admin/facturas", True))
    home_closed = builder.build(payload("/admin/home", False))
    home_opened = builder.build(payload("/admin/home", True))

    assert internal_closed.structural_fingerprint == internal_opened.structural_fingerprint
    assert home_closed.structural_fingerprint != home_opened.structural_fingerprint


def test_region_element_count_does_not_change_structural_signature():
    builder = StateSignatureBuilder()
    base = {
        "path": "/admin/facturas",
        "functional_title": "Facturas",
        "visible_text": "Facturas",
        "main_visible_text": "Facturas",
        "regions": {
            "main_content": {"visible_text": "Facturas", "elements_count": 2},
            "dialog": {"visible_text": "", "elements_count": 0},
        },
        "links": [],
        "buttons": [],
        "inputs": [],
        "tables": [],
        "dialogs": [],
        "custom_interactives": [],
    }
    changed_count = {
        **base,
        "regions": {
            "main_content": {"visible_text": "Facturas", "elements_count": 120},
            "dialog": {"visible_text": "", "elements_count": 0},
        },
    }

    assert (
        builder.build(base).structural_fingerprint
        == builder.build(changed_count).structural_fingerprint
    )
