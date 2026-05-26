from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page

from src.browser.navigator import ERPNavigator
from src.extraction.screen_extractor import ScreenExtractor
from src.graph.routes_graph_builder import RoutesGraphBuilder
from src.graph.screen_index_builder import ScreenIndexBuilder
from src.utils.text_utils import slugify
from src.storage.artifact_storage import ArtifactStorage
from src.policy.route_policy import RoutePolicy
from src.discovery.link_normalizer import LinkNormalizer

class RouteCrawler:
    def __init__(self, page: Page, profile: dict):
        self.page = page
        self.profile = profile
        self.base_url = profile["erp"]["base_url"]
        self.route_policy = RoutePolicy(profile)
        self.max_pages_total = profile["exploration"].get("max_pages_total", 100)

        self.visited: set[str] = set()
        self.pending: list[str] = []
        self.routes_graph = RoutesGraphBuilder()
        self.screen_index = ScreenIndexBuilder()

    def crawl_module(self, module_name: str) -> None:
        navigator = ERPNavigator(self.page, self.profile)
        extractor = ScreenExtractor(self.page, self.profile)
        storage = ArtifactStorage(self.profile)

        print(f"Abriendo módulo inicial: {module_name}")
        opened = navigator.open_module_safe(module_name)

        if not opened:
            print(f"❌ No se pudo abrir el módulo: {module_name}")
            return

        data = extractor.extract_screen_data()
        storage.save_json(data)

        links = self._extract_allowed_links(data)
        self.pending.extend(links)

        print(f"Rutas iniciales encontradas: {len(links)}")

        while self.pending and len(self.visited) < self.max_pages_total:
            route = self.pending.pop(0)

            if route in self.visited:
                continue

            if not self.route_policy.is_allowed(route):
                continue

            print(f"Visitando: {route}")

            try:
                self.page.goto(urljoin(self.base_url, route), wait_until="networkidle")
                self.page.wait_for_timeout(1500)

                self.visited.add(route)

                screen_data = extractor.extract_screen_data()

                route_prefix = route.strip("/").replace("/", "_")

                screenshot_path = storage.save_screenshot(self.page, route_prefix)
                html_path = storage.save_html(self.page, route_prefix)

                screen_data["artifacts"] = {
                    "screenshot": str(screenshot_path),
                    "html": str(html_path),
                }

                screen_data["crawler"] = {
                    "source_module": module_name,
                    "route": route,
                    "visited_order": len(self.visited),
                }

                storage.save_json(screen_data, prefix=route_prefix)

                new_links = self._extract_allowed_links(screen_data)
                self.routes_graph.add_screen(
                    route=route,
                    links=new_links,
                    source_module=module_name,
                )
                self.screen_index.add_screen(
                    route=route,
                    screen_data=screen_data,
                )

                for link in new_links:
                    if link not in self.visited and link not in self.pending:
                        self.pending.append(link)

            except Exception as error:
                print(f"⚠️ Error visitando {route}: {error}")
        
        module_slug = slugify(module_name)

        self.routes_graph.save(
            f"data/processed/structural/{module_slug}_routes_graph.json"
        )

        self.screen_index.save(
            f"data/processed/structural/{module_slug}_screen_index.json"
        )
        print("✅ Crawling finalizado")
        
        print("Pantallas visitadas:", len(self.visited))

    def _extract_allowed_links(self, screen_data: dict) -> list[str]:
        links = []

        for link in screen_data.get("links", []):
            href = link.get("href")

            if not href:
                continue

            normalized = LinkNormalizer.normalize(href)

            if normalized == "/":
                continue

            if normalized and self.route_policy.is_allowed(normalized):
                links.append(normalized)

        return list(dict.fromkeys(links))