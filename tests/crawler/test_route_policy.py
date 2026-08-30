from src.policy.route_policy import RoutePolicy


def build_profile() -> dict:
    return {
        "erp": {
            "base_url": "http://localhost:8080",
        },
        "exploration": {
            "allowed_routes": ["/admin/"],
            "blocked_routes": ["/admin/configuracion"],
        },
        "safety": {
            "dangerous_keywords": ["Eliminar", "Guardar", "Aprobar"],
            "safe_keywords": ["Ver", "Buscar", "Consultar"],
        },
    }


def test_route_policy_allows_valid_admin_routes():
    policy = RoutePolicy(build_profile())

    assert policy.is_allowed_route("/admin/home")
    assert policy.is_allowed_route("/admin/cuentas")
    assert policy.is_allowed_route("/admin/cuentas/facturas")


def test_route_policy_blocks_non_admin_routes():
    policy = RoutePolicy(build_profile())

    assert not policy.is_allowed_route("/")
    assert not policy.is_allowed_route("/login")
    assert not policy.is_allowed_route("/public/home")


def test_route_policy_blocks_config_routes():
    policy = RoutePolicy(build_profile())

    assert not policy.is_allowed_route("/admin/configuracion")
    assert not policy.is_allowed_route("/admin/configuracion/usuarios")


def test_route_policy_normalizes_internal_links():
    policy = RoutePolicy(build_profile())

    assert policy.normalize_href("/admin/home") == "/admin/home"
    assert policy.normalize_href("http://localhost:8080/admin/home") == "/admin/home"
    assert policy.normalize_href("admin/home") == "/admin/home"


def test_route_policy_ignores_invalid_or_external_links():
    policy = RoutePolicy(build_profile())

    assert policy.normalize_href("javascript:void(0)") is None
    assert policy.normalize_href("#") is None
    assert policy.normalize_href("mailto:test@test.com") is None
    assert policy.normalize_href("https://google.com") is None


def test_route_policy_detects_dangerous_actions():
    policy = RoutePolicy(build_profile())

    assert policy.is_dangerous_action_label("Eliminar registro")
    assert policy.is_dangerous_action_label("Guardar cambios")
    assert policy.is_dangerous_action_label("Aprobar solicitud")
    assert not policy.is_dangerous_action_label("Ver detalle")


def test_route_policy_detects_safe_actions():
    policy = RoutePolicy(build_profile())

    assert policy.is_safe_action_label("Ver detalle")
    assert policy.is_safe_action_label("Buscar factura")
    assert policy.is_safe_action_label("Consultar información")
    assert not policy.is_safe_action_label("Eliminar registro")