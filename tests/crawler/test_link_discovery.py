from src.discovery.link_discovery import LinkDiscovery
from src.discovery.link_normalizer import LinkNormalizer
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
            "dangerous_keywords": [],
            "safe_keywords": [],
        },
    }


def test_link_normalizer_normalizes_and_removes_duplicates():
    policy = RoutePolicy(build_profile())
    normalizer = LinkNormalizer(policy)

    links = [
        {"text": "Home", "href": "/admin/home"},
        {"text": "Home duplicado", "href": "http://localhost:8080/admin/home"},
        {"text": "Facturas", "href": "/admin/facturas"},
        {"text": "JS", "href": "javascript:void(0)"},
        {"text": "External", "href": "https://google.com"},
    ]

    result = normalizer.normalize_many(links)

    routes = [item["route"] for item in result]

    assert routes == ["/admin/home", "/admin/facturas"]


def test_link_discovery_returns_only_allowed_routes():
    policy = RoutePolicy(build_profile())
    discovery = LinkDiscovery(policy)

    screen_data = {
        "links": [
            {"text": "Home", "href": "/admin/home"},
            {"text": "Facturas", "href": "/admin/facturas"},
            {"text": "Login", "href": "/login"},
            {"text": "Config", "href": "/admin/configuracion/usuarios"},
            {"text": "External", "href": "https://google.com"},
        ]
    }

    routes = discovery.discover_allowed_routes(screen_data)

    assert routes == ["/admin/home", "/admin/facturas"]


def test_link_discovery_returns_link_metadata():
    policy = RoutePolicy(build_profile())
    discovery = LinkDiscovery(policy)

    screen_data = {
        "links": [
            {
                "text": "Facturas",
                "href": "/admin/facturas",
                "selector": "nav > a:nth-of-type(1)",
                "tag": "a",
            }
        ]
    }

    links = discovery.discover_allowed_links(screen_data)

    assert len(links) == 1
    assert links[0]["route"] == "/admin/facturas"
    assert links[0]["text"] == "Facturas"
    assert links[0]["selector"] == "nav > a:nth-of-type(1)"