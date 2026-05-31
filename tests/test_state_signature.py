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