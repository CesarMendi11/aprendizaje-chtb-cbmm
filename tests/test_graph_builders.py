from src.graph.routes_graph_builder import RoutesGraphBuilder
from src.graph.screen_index_builder import ScreenIndexBuilder


def test_routes_graph_builder_adds_screens():
    builder = RoutesGraphBuilder()

    builder.add_screen(
        route="/admin/home",
        title="Inicio",
        source_module="Home",
    )

    graph = builder.to_dict()

    assert builder.node_count() == 1
    assert graph["nodes"][0]["route"] == "/admin/home"
    assert graph["nodes"][0]["title"] == "Inicio"
    assert graph["nodes"][0]["status"] == "discovered"


def test_routes_graph_builder_avoids_duplicate_screens():
    builder = RoutesGraphBuilder()

    builder.add_screen(route="/admin/home", title="Inicio")
    builder.add_screen(route="/admin/home", title="Inicio duplicado")

    assert builder.node_count() == 1


def test_routes_graph_builder_adds_transitions():
    builder = RoutesGraphBuilder()

    builder.add_screen(route="/admin/home")
    builder.add_screen(route="/admin/facturas")

    builder.add_transition(
        source="/admin/home",
        target="/admin/facturas",
        label="Facturas",
        kind="href",
    )

    graph = builder.to_dict()

    assert builder.edge_count() == 1
    assert graph["edges"][0]["source"] == "/admin/home"
    assert graph["edges"][0]["target"] == "/admin/facturas"
    assert graph["edges"][0]["label"] == "Facturas"


def test_routes_graph_builder_avoids_duplicate_transitions():
    builder = RoutesGraphBuilder()

    builder.add_transition(source="/admin/home", target="/admin/facturas", kind="href")
    builder.add_transition(source="/admin/home", target="/admin/facturas", kind="href")

    assert builder.edge_count() == 1


def test_routes_graph_builder_summary_counts():
    builder = RoutesGraphBuilder()

    builder.add_screen(route="/admin/home")
    builder.add_screen(route="/admin/facturas")
    builder.add_transition(source="/admin/home", target="/admin/facturas")

    graph = builder.to_dict()

    assert graph["summary"]["nodes_count"] == 2
    assert graph["summary"]["edges_count"] == 1


def test_screen_index_builder_adds_screen_summary():
    builder = ScreenIndexBuilder()

    screen_data = {
        "url": "http://localhost:8080/admin/home",
        "path": "/admin/home",
        "title": "Inicio",
        "visible_text": "Panel principal",
        "visible_text_truncated": False,
        "links": [{"text": "Facturas", "href": "/admin/facturas"}],
        "buttons": [{"text": "Buscar"}],
        "inputs": [{"name": "cliente"}],
        "tables": [{"headers": ["Numero"], "rows_count": 1}],
        "custom_interactives": [],
        "artifacts": {
            "html": "data/raw/html/admin_home.html",
            "screenshot": "data/raw/screenshots/admin_home.png",
        },
        "crawler": {
            "depth": 0,
            "reason": "home_url",
        },
    }

    builder.add_screen("/admin/home", screen_data)

    index = builder.to_dict()
    screen = index["screens"][0]

    assert builder.screen_count() == 1
    assert screen["route"] == "/admin/home"
    assert screen["title"] == "Inicio"
    assert screen["status"] == "discovered"
    assert screen["knowledge_origin"] == "discovered"
    assert screen["semantic_status"] == "pending"
    assert screen["links"][0]["text"] == "Facturas"


def test_screen_index_builder_replaces_screen_if_added_again():
    builder = ScreenIndexBuilder()

    builder.add_screen(
        "/admin/home",
        {
            "title": "Inicio viejo",
            "path": "/admin/home",
        },
    )

    builder.add_screen(
        "/admin/home",
        {
            "title": "Inicio nuevo",
            "path": "/admin/home",
        },
    )

    assert builder.screen_count() == 1

    screen = builder.get_screen("/admin/home")

    assert screen is not None
    assert screen["title"] == "Inicio nuevo"