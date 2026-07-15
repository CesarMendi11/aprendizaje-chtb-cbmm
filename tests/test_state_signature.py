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
