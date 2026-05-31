from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page

from src.browser.navigator import ERPNavigator
from src.crawler.frontier import CrawlTarget, Frontier
from src.crawler.state_signature import StateSignatureBuilder
from src.crawler.ui_event_explorer import UIEventExplorer
from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.discovery.link_discovery import LinkDiscovery
from src.extraction.screen_extractor import ScreenExtractor
from src.graph.routes_graph_builder import RoutesGraphBuilder
from src.graph.screen_index_builder import ScreenIndexBuilder
from src.policy.route_policy import RoutePolicy
from src.storage.artifact_storage import ArtifactStorage, safe_slug


@dataclass
class CrawlSummary:
    visited_count: int
    pending_count: int
    nodes_count: int
    edges_count: int
    routes_graph_path: str
    screen_index_path: str


class RouteCrawler:
    """
    Crawler estructural del ERP.

    Responsabilidad:
    - Iniciar desde home_url.
    - Capturar pantalla actual.
    - Descubrir links permitidos.
    - Explorar eventos UI seguros estilo Crawljax.
    - Manejar pending y visited.
    - Guardar HTML, screenshots y JSON crudo.
    - Construir routes_graph y screen_index.

    Este componente NO hace inferencia semántica.
    Este componente NO llama al LLM todavía.
    Este componente NO inserta directamente en Neo4j.
    """

    def __init__(self, page: Page, profile: dict[str, Any]):
        self.page = page
        self.profile = profile

        self.navigator = ERPNavigator(page, profile)
        self.policy = RoutePolicy(profile)
        self.extractor = ScreenExtractor(page, profile)
        self.discovery = LinkDiscovery(self.policy)
        self.storage = ArtifactStorage(profile)

        self.frontier = Frontier()
        self.routes_graph = RoutesGraphBuilder()
        self.screen_index = ScreenIndexBuilder()

        self.candidate_discovery = EventCandidateDiscovery(profile, self.policy)
        self.state_signature_builder = StateSignatureBuilder()
        self.ui_event_explorer = UIEventExplorer(
            page=page,
            profile=profile,
            extractor=self.extractor,
            candidate_discovery=self.candidate_discovery,
            state_signature_builder=self.state_signature_builder,
        )

        exploration = profile.get("exploration", {})
        self.max_depth = exploration.get("max_depth", 5)
        self.max_pages_total = exploration.get("max_pages_total", 300)
        self.page_wait_ms = exploration.get("page_wait_ms", 1500)
        self.start_modules = exploration.get("start_modules", [])

        ui_events = profile.get("ui_events", {})
        self.ui_events_enabled = ui_events.get("enabled", True)
        self.max_event_depth = ui_events.get("max_event_depth", 0)

    def crawl(self) -> CrawlSummary:
        """
        Ejecuta el descubrimiento estructural completo.

        Si el usuario interrumpe con CTRL + C,
        guarda los resultados parciales antes de salir.
        """

        try:
            self.navigator.goto_home()

            self._capture_current_screen(
                source="root",
                depth=0,
                reason="home_url",
            )

            self._checkpoint_outputs()

            self._open_start_modules_if_configured()
            self._checkpoint_outputs()

            self._crawl_pending_targets()

        except KeyboardInterrupt:
            print("\nInterrupción detectada dentro del crawler.")
            print("Guardando resultados parciales antes de salir...")

        return self._save_outputs()

    def _open_start_modules_if_configured(self) -> None:
        if not self.start_modules:
            return

        for module_name in self.start_modules:
            opened = self.navigator.click_text_if_visible(module_name, exact=False)

            if not opened:
                self._save_uncertainty(
                    route=self.navigator.current_path(),
                    reason="start_module_not_found",
                    extra={
                        "module_name": module_name,
                        "message": "No se pudo abrir el módulo inicial configurado.",
                    },
                )
                continue

            self._capture_current_screen(
                source="start_module",
                depth=0,
                reason=f"opened_start_module:{module_name}",
            )

    def _crawl_pending_targets(self) -> None:
        while self.frontier.has_pending():
            if self.frontier.visited_count() >= self.max_pages_total:
                break

            target = self.frontier.pop()

            if target is None:
                break

            if target.depth > self.max_depth:
                continue

            if self.frontier.is_visited(target.route):
                continue

            if not self.policy.is_allowed_route(target.route):
                continue

            try:
                self.navigator.goto_path(target.route)

                if self.page_wait_ms:
                    self.page.wait_for_timeout(self.page_wait_ms)

                self._capture_current_screen(
                    source=target.source,
                    depth=target.depth,
                    reason=target.reason,
                )

            except Exception as error:
                self._save_uncertainty(
                    route=target.route,
                    reason="navigation_error",
                    extra={
                        "source": target.source,
                        "depth": target.depth,
                        "error": str(error),
                    },
                )

    def _capture_current_screen(self, source: str, depth: int, reason: str) -> None:
        screen_data = self.extractor.extract()

        route = screen_data.get("path") or self.navigator.current_path()

        if not self.policy.is_allowed_route(route):
            return

        if self.frontier.is_visited(route):
            return

        self.frontier.mark_visited(route)

        prefix = self._build_artifact_prefix(route)

        self._save_screen_artifacts(
            route=route,
            screen_data=screen_data,
            prefix=prefix,
            source=source,
            depth=depth,
            reason=reason,
        )

        self._register_screen(
            route=route,
            screen_data=screen_data,
            source=source,
            depth=depth,
            reason=reason,
        )

        discovered_links = self.discovery.discover_allowed_links(screen_data)

        self._register_discovered_links(
            source_route=route,
            links=discovered_links,
            depth=depth,
            reason="href_discovered",
        )

        if self.ui_events_enabled and depth <= self.max_event_depth:
            self._explore_ui_events_from_screen(
                route=route,
                screen_data=screen_data,
                depth=depth,
            )

        self._detect_and_store_uncertainty(
            route=route,
            screen_data=screen_data,
            discovered_links=discovered_links,
        )

        self._checkpoint_outputs()

    def _save_screen_artifacts(
        self,
        route: str,
        screen_data: dict[str, Any],
        prefix: str,
        source: str,
        depth: int,
        reason: str,
    ) -> None:
        html_path = self.storage.save_html_content(
            html=self.navigator.get_html(),
            prefix=prefix,
        )

        screenshot_path = self.storage.save_screenshot_bytes(
            content=self.navigator.screenshot_bytes(full_page=True),
            prefix=prefix,
        )

        screen_data["artifacts"] = {
            "html": str(html_path),
            "screenshot": str(screenshot_path),
        }

        screen_data["crawler"] = {
            "route": route,
            "source": source,
            "depth": depth,
            "reason": reason,
            "status": "discovered",
        }

        raw_json_path = self.storage.save_raw_screen_json(
            data=screen_data,
            prefix=prefix,
        )

        screen_data["artifacts"]["raw_json"] = str(raw_json_path)

    def _register_screen(
        self,
        route: str,
        screen_data: dict[str, Any],
        source: str,
        depth: int,
        reason: str,
    ) -> None:
        self.routes_graph.add_screen(
            route=route,
            title=screen_data.get("title", ""),
            source_module=source,
            status="discovered",
            metadata={
                "reason": reason,
                "depth": depth,
            },
        )

        self.screen_index.add_screen(
            route=route,
            screen_data=screen_data,
            status="discovered",
        )

    def _register_discovered_links(
        self,
        source_route: str,
        links: list[dict[str, Any]],
        depth: int,
        reason: str,
        only_new_targets: bool = False,
    ) -> None:
        for link in links:
            target_route = link["route"]

            if target_route == source_route:
                continue

            if not self.policy.is_allowed_route(target_route):
                continue
            
            if only_new_targets and self.routes_graph.has_screen(target_route):
               continue

            self.routes_graph.add_screen(
                route=target_route,
                title=link.get("text", ""),
                source_module=source_route,
                status="discovered",
                metadata={
                    "discovered_from": source_route,
                },
            )

            self.routes_graph.add_transition(
                source=source_route,
                target=target_route,
                label=link.get("text", ""),
                kind=reason,
                metadata={
                    "selector": link.get("selector", ""),
                    "href": link.get("href", ""),
                },
            )

            self.frontier.push(
                CrawlTarget(
                    route=target_route,
                    source=source_route,
                    depth=depth + 1,
                    reason=reason,
                )
            )

    def _explore_ui_events_from_screen(
        self,
        route: str,
        screen_data: dict[str, Any],
        depth: int,
    ) -> None:
        results = self.ui_event_explorer.explore_current_state(screen_data)

        changed_results = [
            result for result in results
            if result.changed and result.error is None
        ]

        if not results:
            return

        self._save_ui_event_results(
            route=route,
            results=[result.to_dict() for result in results],
        )

        for result in changed_results:
            event_prefix = self._build_ui_state_prefix(
                route=route,
                fingerprint=result.after_fingerprint,
            )

            after_screen_data = result.after_screen_data

            html_path = self.storage.save_html_content(
                html=self.navigator.get_html(),
                prefix=event_prefix,
            )

            screenshot_path = self.storage.save_screenshot_bytes(
                content=self.navigator.screenshot_bytes(full_page=True),
                prefix=event_prefix,
            )

            after_screen_data["artifacts"] = {
                "html": str(html_path),
                "screenshot": str(screenshot_path),
            }

            after_screen_data["crawler"] = {
                "route": after_screen_data.get("path") or route,
                "source": route,
                "depth": depth,
                "reason": "ui_event_state_change",
                "status": "discovered",
                "ui_event_candidate": result.candidate,
                "before_fingerprint": result.before_fingerprint,
                "after_fingerprint": result.after_fingerprint,
            }

            raw_json_path = self.storage.save_raw_screen_json(
                data=after_screen_data,
                prefix=event_prefix,
            )

            after_screen_data["artifacts"]["raw_json"] = str(raw_json_path)

            event_node_id = f"{route}#state:{result.after_fingerprint[:12]}"

            self.routes_graph.add_screen(
                route=event_node_id,
                title=after_screen_data.get("title", ""),
                source_module=route,
                status="discovered",
                metadata={
                    "kind": "ui_state",
                    "base_route": route,
                    "before_fingerprint": result.before_fingerprint,
                    "after_fingerprint": result.after_fingerprint,
                    "candidate": result.candidate,
                },
            )

            self.routes_graph.add_transition(
                source=route,
                target=event_node_id,
                label=result.candidate.get("label", ""),
                kind="ui_event",
                metadata={
                    "event_type": result.candidate.get("event_type"),
                    "action_kind": result.candidate.get("action_kind"),
                    "selector": result.candidate.get("selector"),
                },
            )

            discovered_links = self.discovery.discover_allowed_links(after_screen_data)

            self._register_discovered_links(
                source_route=event_node_id,
                links=discovered_links,
                depth=depth,
                reason="ui_event_discovered_href",
                only_new_targets=True,
            )

    def _save_ui_event_results(
        self,
        route: str,
        results: list[dict[str, Any]],
    ) -> None:
        slim_results = []

        for result in results:
            after_screen_data = result.get("after_screen_data", {})

            slim_results.append(
                {
                    "candidate": result.get("candidate", {}),
                    "changed": result.get("changed"),
                    "before_fingerprint": result.get("before_fingerprint"),
                    "after_fingerprint": result.get("after_fingerprint"),
                    "before_route": result.get("before_route"),
                    "after_route": result.get("after_route"),
                    "error": result.get("error"),
                    "after_summary": {
                        "title": after_screen_data.get("title"),
                        "path": after_screen_data.get("path"),
                        "links_count": len(after_screen_data.get("links", [])),
                        "buttons_count": len(after_screen_data.get("buttons", [])),
                        "inputs_count": len(after_screen_data.get("inputs", [])),
                        "custom_interactives_count": len(
                            after_screen_data.get("custom_interactives", [])
                        ),
                    },
                }
            )

        payload = {
            "route": route,
            "status": "ui_events_explored",
            "results_count": len(slim_results),
            "results": slim_results,
        }

        self.storage.save_uncertainty_json(
            data=payload,
            prefix=f"{route}_ui_events",
        )

    def _detect_and_store_uncertainty(
        self,
        route: str,
        screen_data: dict[str, Any],
        discovered_links: list[dict[str, Any]],
    ) -> None:
        reasons = []

        custom_interactives = screen_data.get("custom_interactives", [])
        buttons = screen_data.get("buttons", [])
        inputs = screen_data.get("inputs", [])

        if custom_interactives and not discovered_links:
            reasons.append(
                "Hay elementos interactivos personalizados, pero no se descubrieron rutas href."
            )

        if len(custom_interactives) >= 10:
            reasons.append(
                "La pantalla contiene muchos elementos interactivos personalizados."
            )

        dangerous_buttons = [
            button
            for button in buttons
            if self.policy.is_dangerous_action_label(button.get("text"))
        ]

        if dangerous_buttons:
            reasons.append(
                "La pantalla contiene acciones peligrosas que no deben ejecutarse automáticamente."
            )

        if inputs and buttons and not discovered_links:
            reasons.append(
                "La pantalla parece depender de formularios o búsqueda para mostrar nuevos estados."
            )

        if not reasons:
            return

        self._save_uncertainty(
            route=route,
            reason="uncertain_screen",
            extra={
                "reasons": reasons,
                "title": screen_data.get("title", ""),
                "url": screen_data.get("url", ""),
                "buttons": buttons,
                "inputs": inputs,
                "custom_interactives": custom_interactives,
                "artifacts": screen_data.get("artifacts", {}),
                "next_step": "Enviar a analysis/LLM helper para generar YAML incremental.",
            },
        )

    def _save_uncertainty(
        self,
        route: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "route": route,
            "reason": reason,
            "extra": extra or {},
            "status": "pending_llm_or_human_review",
        }

        self.storage.save_uncertainty_json(
            data=payload,
            prefix=f"{route}_{reason}",
        )

    def _checkpoint_outputs(self) -> None:
        """
        Guarda una copia parcial del grafo y del índice.

        Esto evita perder todo si el crawler se interrumpe.
        """

        self.storage.save_processed_structural_json(
            data=self.routes_graph.to_dict(),
            filename="routes_graph.partial.json",
        )

        self.storage.save_processed_structural_json(
            data=self.screen_index.to_dict(),
            filename="screen_index.partial.json",
        )

    def _save_outputs(self) -> CrawlSummary:
        routes_graph_data = self.routes_graph.to_dict()
        screen_index_data = self.screen_index.to_dict()

        routes_graph_path = self.storage.save_processed_structural_json(
            data=routes_graph_data,
            filename="routes_graph.json",
        )

        screen_index_path = self.storage.save_processed_structural_json(
            data=screen_index_data,
            filename="screen_index.json",
        )

        return CrawlSummary(
            visited_count=self.frontier.visited_count(),
            pending_count=self.frontier.pending_count(),
            nodes_count=self.routes_graph.node_count(),
            edges_count=self.routes_graph.edge_count(),
            routes_graph_path=str(routes_graph_path),
            screen_index_path=str(screen_index_path),
        )

    def _build_artifact_prefix(self, route: str) -> str:
        return safe_slug(route, fallback="screen")

    def _build_ui_state_prefix(self, route: str, fingerprint: str) -> str:
        route_slug = safe_slug(route, fallback="screen")
        return f"{route_slug}_state_{fingerprint[:12]}"