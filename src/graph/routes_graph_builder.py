import json
from pathlib import Path


class RoutesGraphBuilder:
    def __init__(self):
        self.graph = {}

    def add_screen(self, route: str, links: list[str], source_module: str):
        clean_links = sorted(list(set(
            link for link in links
            if link != route
        )))

        self.graph[route] = {
            "source_module": source_module,
            "links_to": clean_links,
        }

    def save(self, output_path: str):
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as file:
            json.dump(self.graph, file, indent=2, ensure_ascii=False)