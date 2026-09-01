from __future__ import annotations

from erp_assistant.acquisition.crawling.route_crawler import RouteCrawler


class FakePolicy:
    def normalize_href(self, route):
        return str(route or "").split("#", 1)[0].split("?", 1)[0]

    def is_allowed_route(self, route):
        return bool(route and str(route).startswith("/admin/"))


def scoped_crawler(scope=None):
    crawler = object.__new__(RouteCrawler)
    crawler.route_scope = scope
    crawler.policy = FakePolicy()
    return crawler


def test_screen_route_scope_is_exact_by_pathname_but_ignores_query_and_trailing_slash():
    crawler = scoped_crawler({"/admin/cuentasxcobrar/retenciones"})
    assert crawler._is_allowed_route("/admin/cuentasxcobrar/retenciones")
    assert crawler._is_allowed_route("/admin/cuentasxcobrar/retenciones?pagina=2")
    assert crawler._is_allowed_route("/admin/cuentasxcobrar/retenciones/")
    assert not crawler._is_allowed_route("/admin/cuentasxcobrar/retenciones/nueva")
    assert not crawler._is_allowed_route("/admin/clientes")


def test_full_scope_preserves_existing_route_policy_behavior():
    crawler = scoped_crawler(None)
    assert crawler._is_allowed_route("/admin/facturas")
    assert crawler._is_allowed_route("/admin/clientes")
    assert not crawler._is_allowed_route("/public/login")


def test_progress_callback_is_optional_for_legacy_object_new_tests():
    crawler = object.__new__(RouteCrawler)
    crawler.frontier = type(
        "Frontier",
        (),
        {"visited_count": lambda self: 0, "pending_count": lambda self: 0},
    )()
    crawler.state_frontier = type(
        "StateFrontier",
        (),
        {"explored_count": lambda self: 0, "pending_count": lambda self: 0},
    )()
    crawler.screen_index = type("ScreenIndex", (), {"screen_count": lambda self: 0})()
    crawler.routes_graph = type(
        "RoutesGraph",
        (),
        {"node_count": lambda self: 0, "edge_count": lambda self: 0},
    )()
    crawler.state_flow_graph = type(
        "StateFlow",
        (),
        {"state_count": lambda self: 0, "transition_count": lambda self: 0},
    )()
    crawler._unavailable_routes = set()
    crawler._emit_progress("test")


def test_direct_screen_crawl_forwards_pinned_canonical_title_to_initial_capture():
    route = "/admin/permisos/consultar/aprobados/3"
    crawler = object.__new__(RouteCrawler)
    crawler.policy = FakePolicy()
    crawler.route_scope = {route}
    crawler.page_wait_ms = 0
    crawler.navigator = type(
        "Navigator",
        (),
        {
            "goto_path": lambda self, value: None,
            "current_path": lambda self: route,
        },
    )()
    crawler._emit_progress = lambda *args, **kwargs: None
    crawler._checkpoint_outputs = lambda: None
    crawler._crawl_until_fixed_point = lambda: None
    sentinel = object()
    crawler._save_outputs = lambda: sentinel
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)

    crawler._capture_current_screen = capture

    result = crawler.crawl_screen(
        route,
        canonical_title="Permisos Espectáculo Público",
    )

    assert result is sentinel
    assert captured == {
        "source": "screen_scope",
        "depth": 0,
        "reason": "screen_scope_target",
        "canonical_title": "Permisos Espectáculo Público",
    }
