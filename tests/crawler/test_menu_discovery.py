from src.discovery.menu_discovery import MenuDiscovery


def test_menu_discovery_extracts_collapsable_fuse_items():
    screen_data = {
        "custom_interactives": [
            {
                "text": "MENU DE NAVEGACIÓN Dashboard Cuentas por cobrar General",
                "tag": "fuse-vertical-navigation",
                "selector": "fuse-vertical-navigation",
            },
            {
                "text": "Cuentas por cobrar",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "selector": "fuse-vertical-navigation-collapsable-item:nth-of-type(1)",
            },
            {
                "text": "General",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "selector": "fuse-vertical-navigation-collapsable-item:nth-of-type(2)",
            },
            {
                "text": "Dashboard",
                "tag": "fuse-vertical-navigation-basic-item",
                "selector": "fuse-vertical-navigation-basic-item",
            },
        ]
    }

    discovery = MenuDiscovery()
    candidates = discovery.discover_menu_candidates(screen_data)

    labels = [candidate["label"] for candidate in candidates]

    assert "Cuentas por cobrar" in labels
    assert "General" in labels
    assert "Dashboard" in labels
    assert all("MENU DE NAVEGACIÓN" not in label for label in labels)


def test_menu_discovery_returns_only_collapsable_candidates():
    screen_data = {
        "custom_interactives": [
            {
                "text": "Dashboard",
                "tag": "fuse-vertical-navigation-basic-item",
                "selector": "basic",
            },
            {
                "text": "Gerencial",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "selector": "collapsable",
            },
        ]
    }

    discovery = MenuDiscovery()
    candidates = discovery.discover_collapsable_menu_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0]["label"] == "Gerencial"
    assert candidates[0]["kind"] == "collapsable_menu"


def test_menu_discovery_removes_duplicates_by_label():
    screen_data = {
        "custom_interactives": [
            {
                "text": "Permisos",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "selector": "one",
            },
            {
                "text": "Permisos",
                "tag": "fuse-vertical-navigation-collapsable-item",
                "selector": "two",
            },
        ]
    }

    discovery = MenuDiscovery()
    candidates = discovery.discover_menu_candidates(screen_data)

    assert len(candidates) == 1
    assert candidates[0]["label"] == "Permisos"


def test_menu_discovery_ignores_large_container_text():
    screen_data = {
        "custom_interactives": [
            {
                "text": "MENU DE NAVEGACIÓN Dashboard Cuentas por cobrar General Gerencial Permisos Prevencion Riesgo rentas Rentas Seguridad SRI Tramites",
                "tag": "fuse-vertical-navigation",
                "selector": "container",
            }
        ]
    }

    discovery = MenuDiscovery()
    candidates = discovery.discover_menu_candidates(screen_data)

    assert candidates == []