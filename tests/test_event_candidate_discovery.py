from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.policy.route_policy import RoutePolicy


def build_profile() -> dict:
    return {
        "erp": {
            "base_url": "http://localhost:8080",
        },
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": [],
        },
        "safety": {
            "dangerous_keywords": [
                "Guardar",
                "Eliminar",
                "Aprobar",
                "Emitir",
            ],
            "safe_keywords": [
                "Ver",
                "Buscar",
                "Consultar",
                "Detalle",
                "Cerrar",
                "Cancelar",
            ],
        },
        "ui_events": {
            "enabled": True,
            "min_candidate_score": 3,
            "candidate_limits": {
                "max_candidates_per_screen": 80,
                "max_text_length": 100,
            },
        },
        "forms": {
            "enabled": False,
            "allow_submit": False,
        },
    }


def build_discovery() -> EventCandidateDiscovery:
    profile = build_profile()
    policy = RoutePolicy(profile)
    return EventCandidateDiscovery(profile, policy)


def test_event_candidate_discovery_detects_native_buttons():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Buscar",
                "tag": "button",
                "type": None,
                "selector": "button.search",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0].label == "Buscar"
    assert candidates[0].action_kind == "button_click"
    assert candidates[0].dangerous is False


def test_event_candidate_discovery_marks_dangerous_buttons():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Eliminar factura",
                "tag": "button",
                "type": None,
                "selector": "button.delete",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0].dangerous is True
    assert "dangerous_text_detected" in candidates[0].reasons


def test_event_candidate_discovery_detects_collapsable_custom_element():
    discovery = build_discovery()

    screen_data = {
        "buttons": [],
        "links": [],
        "custom_interactives": [
            {
                "text": "Cuentas por cobrar",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "selector": "fuse-vertical-navigation-collapsable-item:nth-of-type(1)",
                "role": None,
                "aria_expanded": None,
                "onclick": False,
            }
        ],
    }

    candidates = discovery.discover_candidates(screen_data)

    labels = [candidate.label for candidate in candidates]

    assert "Cuentas por cobrar" in labels

    candidate = candidates[0]

    assert candidate.action_kind in {
        "expand_or_collapse",
        "navigation_click",
        "generic_ui_click",
    }

    assert candidate.dangerous is False


def test_event_candidate_discovery_ignores_giant_menu_container():
    discovery = build_discovery()

    screen_data = {
        "buttons": [],
        "links": [],
        "custom_interactives": [
            {
                "text": "MENU DE NAVEGACIÓN Dashboard Cuentas por cobrar General Gerencial Permisos Prevencion Riesgo rentas Rentas Seguridad SRI Tramites",
                "tag": "fuse-vertical-navigation",
                "selector": "fuse-vertical-navigation",
                "role": None,
                "aria_expanded": None,
                "onclick": False,
            }
        ],
    }

    candidates = discovery.discover_candidates(screen_data)

    assert candidates == []


def test_event_candidate_discovery_blocks_submit_when_forms_not_allowed():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Enviar",
                "tag": "button",
                "type": "submit",
                "selector": "button[type='submit']",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0].dangerous is True
    assert "submit_button_blocked_by_forms_policy" in candidates[0].reasons


def test_event_candidate_discovery_returns_only_safe_candidates():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Buscar",
                "tag": "button",
                "type": None,
                "selector": "button.search",
            },
            {
                "text": "Eliminar",
                "tag": "button",
                "type": None,
                "selector": "button.delete",
            },
        ],
        "links": [],
        "custom_interactives": [],
    }

    safe_candidates = discovery.discover_safe_candidates(screen_data)

    assert len(safe_candidates) == 1
    assert safe_candidates[0].label == "Buscar"


def test_event_candidate_discovery_deduplicates_by_selector():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Buscar",
                "tag": "button",
                "type": None,
                "selector": "button.search",
            },
            {
                "text": "Buscar duplicado",
                "tag": "button",
                "type": None,
                "selector": "button.search",
            },
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_candidates(screen_data)

    assert len(candidates) == 1

def test_event_candidate_discovery_classifies_and_allows_menu_expansion():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Abrir menú",
                "tag": "button",
                "type": None,
                "selector": "button.open-menu",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0].event_category == "expand_menu"
    assert candidates[0].decision == "allow"
    assert candidates[0].risk_level == "low"


def test_event_candidate_discovery_does_not_execute_unknown_action():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Ejecutar operación especial",
                "tag": "button",
                "type": None,
                "selector": "button.special",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_candidates(screen_data)
    safe_candidates = discovery.discover_safe_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0].event_category == "unknown"
    assert candidates[0].decision == "review"
    assert safe_candidates == []


def test_event_candidate_discovery_blocks_mutative_hint_not_in_profile_keywords():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Registrar novedad",
                "tag": "button",
                "type": None,
                "selector": "button.register",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidate = discovery.discover_candidates(screen_data)[0]

    assert candidate.event_category == "mutative_action"
    assert candidate.decision == "deny"
    assert candidate.dangerous is True


def test_event_candidate_discovery_allows_search_submit_without_general_form_submit():
    discovery = build_discovery()

    screen_data = {
        "buttons": [
            {
                "text": "Buscar",
                "tag": "button",
                "type": "submit",
                "selector": "button.search",
            }
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidate = discovery.discover_candidates(screen_data)[0]

    assert candidate.event_category == "submit_search"
    assert candidate.decision == "allow"
    assert candidate.dangerous is False

def test_discovery_classifies_accented_open_menu_as_safe_expand_menu():
    profile = build_profile()
    policy = RoutePolicy(profile)
    discovery = EventCandidateDiscovery(profile, policy)

    screen_data = {
        "links": [],
        "buttons": [
            {
                "text": "Abrir menú",
                "tag": "button",
                "type": None,
                "role": None,
                "selector": "html > body > button",
            }
        ],
        "custom_interactives": [],
    }

    candidates = discovery.discover_safe_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0].event_category == "expand_menu"
    assert candidates[0].decision == "allow"



def test_safe_candidates_prioritize_local_controls_outside_home():
    discovery = build_discovery()
    discovery.home_route = "/admin/home"

    screen_data = {
        "path": "/admin/facturas",
        "buttons": [
            {
                "text": "Buscar",
                "tag": "button",
                "type": None,
                "selector": "main button.search",
                "region": "main_content",
            },
            {
                "text": "Abrir menú",
                "tag": "button",
                "type": None,
                "selector": "aside button.open-menu",
                "region": "global_navigation",
            },
        ],
        "links": [],
        "custom_interactives": [],
    }

    candidates = discovery.discover_safe_candidates(screen_data)

    assert [candidate.label for candidate in candidates[:2]] == [
        "Buscar",
        "Abrir menú",
    ]
    assert candidates[0].metadata["region"] == "main_content"
