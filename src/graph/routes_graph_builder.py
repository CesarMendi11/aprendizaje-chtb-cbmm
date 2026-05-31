from __future__ import annotations

from typing import Any


class RoutesGraphBuilder:
    """
    Construye el grafo estructural de navegación del ERP.

    Responsabilidad:
    - Registrar pantallas como nodos.
    - Registrar relaciones entre pantallas como aristas.
    - Evitar nodos y relaciones duplicadas.
    - Generar una estructura JSON lista para persistir o importar a Neo4j.

    Este componente NO navega.
    Este componente NO extrae pantallas.
    Este componente NO guarda archivos.
    """

    def __init__(self):
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, Any]] = []
        self._edge_keys: set[tuple[str, str, str]] = set()

    def add_screen(
        self,
        route: str,
        title: str = "",
        source_module: str | None = None,
        status: str = "discovered",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not route:
            raise ValueError("route no puede estar vacío.")

        if route in self._nodes:
            existing = self._nodes[route]

            if title and not existing.get("title"):
                existing["title"] = title

            if source_module and not existing.get("source_module"):
                existing["source_module"] = source_module

            existing["metadata"].update(metadata or {})
            return

        self._nodes[route] = {
            "id": route,
            "type": "screen",
            "route": route,
            "title": title,
            "source_module": source_module,
            "status": status,
            "metadata": metadata or {},
        }

    def add_transition(
        self,
        source: str,
        target: str,
        label: str = "",
        kind: str = "href",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not source:
            raise ValueError("source no puede estar vacío.")

        if not target:
            raise ValueError("target no puede estar vacío.")

        edge_key = (source, target, kind)

        if edge_key in self._edge_keys:
            return

        self._edge_keys.add(edge_key)

        self._edges.append(
            {
                "source": source,
                "target": target,
                "label": label,
                "kind": kind,
                "metadata": metadata or {},
            }
        )

    def node_count(self) -> int:
        return len(self._nodes)

    def edge_count(self) -> int:
        return len(self._edges)

    def has_screen(self, route: str) -> bool:
        return route in self._nodes

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_type": "erp_navigation_graph",
            "nodes": list(self._nodes.values()),
            "edges": self._edges,
            "summary": {
                "nodes_count": self.node_count(),
                "edges_count": self.edge_count(),
            },
        }