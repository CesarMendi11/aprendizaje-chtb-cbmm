from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import Page

from src.browser.navigator import ERPNavigator
from src.crawler.module_scope import ModuleCrawlBoundary
from src.crawler.frontier import CrawlTarget, Frontier
from src.crawler.path_replayer import PathReplayer
from src.crawler.screen_availability import ScreenAvailabilityClassifier
from src.crawler.state_frontier import StateFrontier
from src.crawler.state_observer import StableStateObserver
from src.crawler.state_registry import StateRegistry
from src.crawler.state_restorer import StateRestorer
from src.crawler.state_signature import StateSignatureBuilder
from src.crawler.ui_event_explorer import UIEventExplorer
from src.discovery.event_candidate_discovery import EventCandidateDiscovery
from src.discovery.link_discovery import LinkDiscovery
from src.extraction.screen_extractor import ScreenExtractor
from src.graph.routes_graph_builder import RoutesGraphBuilder
from src.graph.screen_index_builder import ScreenIndexBuilder
from src.graph.state_flow_graph_builder import StateFlowGraphBuilder
from src.models.crawl_path import CrawlPath, CrawlPathStep
from src.models.transition import Transition
from src.models.ui_event import EventDecision, RiskLevel, UIEvent, UIEventType
from src.models.ui_state import UIState
from src.policy.route_policy import RoutePolicy
from src.review.event_policy_auditor import build_event_policy_audit
from src.storage.artifact_storage import ArtifactStorage, safe_slug


@dataclass
class CrawlSummary:
    visited_count: int
    pending_count: int
    nodes_count: int
    edges_count: int
    routes_graph_path: str
    screen_index_path: str
    states_count: int = 0
    state_transitions_count: int = 0
    state_flow_graph_path: str = ""
    state_frontier_pending_count: int = 0
    state_frontier_explored_count: int = 0
    functional_screen_count: int = 0
    unavailable_count: int = 0


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

    def __init__(
        self,
        page: Page,
        profile: dict[str, Any],
        *,
        route_scope: set[str] | None = None,
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.page = page
        self.profile = profile
        self.route_scope = (
            {self._route_identity(route) for route in route_scope}
            if route_scope is not None
            else None
        )
        self.module_boundary: ModuleCrawlBoundary | None = None
        self._module_entry_depth = 0
        self._module_dynamic_routes: set[str] = set()
        self.progress_callback = progress_callback

        self.navigator = ERPNavigator(page, profile)
        self.policy = RoutePolicy(profile)
        self.extractor = ScreenExtractor(page, profile)
        self.discovery = LinkDiscovery(self.policy)
        self.storage = ArtifactStorage(profile)
        self.screen_availability = ScreenAvailabilityClassifier(profile)

        self.frontier = Frontier()
        self._unavailable_routes: set[str] = set()
        self.routes_graph = RoutesGraphBuilder()
        self.screen_index = ScreenIndexBuilder()

        self.candidate_discovery = EventCandidateDiscovery(profile, self.policy)
        self.state_signature_builder = StateSignatureBuilder.from_profile(profile)

        self.state_registry = StateRegistry()
        self.state_frontier = StateFrontier()
        self.state_flow_graph = StateFlowGraphBuilder()
        self.state_replay_enabled = bool(
            profile.get("state_replay", {}).get("enabled", True)
        )
        self.path_replayer = PathReplayer(
            page=page,
            profile=profile,
            navigator=self.navigator,
            extractor=self.extractor,
            signature_builder=self.state_signature_builder,
            registry=self.state_registry,
        )
        self.state_restorer = StateRestorer(
            profile=profile,
            navigator=self.navigator,
            extractor=self.extractor,
            signature_builder=self.state_signature_builder,
            registry=self.state_registry,
            path_replayer=self.path_replayer,
        )
        self.ui_event_explorer = UIEventExplorer(
            page=page,
            profile=profile,
            extractor=self.extractor,
            candidate_discovery=self.candidate_discovery,
            state_signature_builder=self.state_signature_builder,
            state_restorer=(
                self.state_restorer if self.state_replay_enabled else None
            ),
        )

        exploration = profile.get("exploration", {})
        self.max_depth = exploration.get("max_depth", 5)
        self.max_pages_total = exploration.get("max_pages_total", 300)
        self.page_wait_ms = exploration.get("page_wait_ms", 1500)
        self.start_modules = exploration.get("start_modules", [])
        self.home_route = profile.get("navigation", {}).get("home_url", "")

        ui_events = profile.get("ui_events", {})
        self.ui_events_enabled = ui_events.get("enabled", True)
        self.max_event_depth = max(0, int(ui_events.get("max_event_depth", 0)))
        self.home_navigation_enabled = bool(
            ui_events.get("home_navigation_enabled", True)
        )
        self.explore_local_route_roots = bool(
            ui_events.get("explore_local_route_roots", True)
        )
        self.recursive_state_exploration = bool(
            ui_events.get("recursive_state_exploration", True)
        )
        self.home_event_categories = set(
            ui_events.get("home_event_categories", ["expand_menu"])
        )
        self.local_event_categories = set(
            ui_events.get(
                "local_event_categories",
                [
                    "activate_tab",
                    "open_readonly_view",
                    "open_date_picker",
                    "open_modal",
                    "open_dropdown",
                    "change_pagination",
                ],
            )
        )

    def crawl(self) -> CrawlSummary:
        """
        Ejecuta el descubrimiento estructural completo.

        Si el usuario interrumpe con CTRL + C,
        guarda los resultados parciales antes de salir.
        """

        try:
            self._emit_progress("navigating_home")
            self.navigator.goto_home()

            self._capture_current_screen(
                source="root",
                depth=0,
                reason="home_url",
            )

            self._checkpoint_outputs()

            self._open_start_modules_if_configured()
            self._checkpoint_outputs()

            self._crawl_until_fixed_point()

        except KeyboardInterrupt:
            print("\nInterrupción detectada dentro del crawler.")
            print("Guardando resultados parciales antes de salir...")

        return self._save_outputs()

    def crawl_module(self, boundary: ModuleCrawlBoundary) -> CrawlSummary:
        """Recorre las pantallas conocidas de un subárbol MODULE fijado.

        Este primer executor MODULE conserva una frontera exacta sobre las rutas
        ya conocidas. También reproduce la trayectoria de menús fijada para que
        ``routes_graph`` y ``state_registry`` mantengan la jerarquía navegacional.
        La ampliación dinámica a rutas nuevas se habilitará separadamente, con
        procedencia estructural explícita.
        """
        expected_scope = {
            self._route_identity(route)
            for route in boundary.known_screen_routes
        }
        if self.route_scope is None:
            self.route_scope = expected_scope
        elif self.route_scope != expected_scope:
            raise ValueError(
                "El route_scope configurado no coincide con las rutas fijadas del MODULE"
            )

        self.module_boundary = boundary
        self._module_entry_depth = len(boundary.entry_steps)
        self._module_dynamic_routes = set()

        try:
            self._emit_progress(
                "navigating_module",
                target_module_id=boundary.root_module_id,
            )
            entry_state, entry_node_id = self._enter_module_branch(boundary)
            self._checkpoint_outputs()

            for route in boundary.known_screen_routes:
                if not self._is_allowed_route(route):
                    raise ValueError(
                        f"Ruta fijada fuera de la política del perfil: {route}"
                    )
                self.frontier.push(
                    CrawlTarget(
                        route=route,
                        source=entry_node_id,
                        depth=0,
                        reason="module_scope_known_screen",
                        title_hint="",
                    )
                )

            self.state_frontier.push_state(
                entry_state,
                reason="module_scope_entry_state",
            )
            self._crawl_until_fixed_point()
        except KeyboardInterrupt:
            print("\nInterrupción detectada dentro del crawler de módulo.")
            print("Guardando resultados parciales antes de salir...")

        return self._save_outputs()

    def _enter_module_branch(
        self,
        boundary: ModuleCrawlBoundary,
    ) -> tuple[UIState, str]:
        """Reproduce únicamente la rama de menú fijada del módulo seleccionado."""
        self.navigator.goto_home()
        if self.page_wait_ms:
            self.page.wait_for_timeout(self.page_wait_ms)

        observation = self._observe_screen()
        home_route = observation.screen_data.get("path") or self.navigator.current_path()
        if not self.policy.is_allowed_route(home_route):
            raise RuntimeError("home_url quedó fuera de la política durante MODULE")

        root_signature = observation.signature
        root_state_id = self.state_registry.build_state_id(
            root_signature.structural_fingerprint
        )
        root_path = CrawlPath(
            root_state_id=root_state_id,
            metadata={
                "scope": "module",
                "target_module_id": boundary.root_module_id,
            },
        )
        current_state = self.state_registry.register_signature(
            signature=root_signature,
            path=root_path,
            metadata={
                "source": "module_scope",
                "depth": 0,
                "reason": "module_scope_home",
                "kind": "route_root_state",
                "canonical_title": root_signature.title,
                "state_observation": observation.diagnostics(),
            },
        ).state
        self.state_flow_graph.add_state(current_state)
        self.routes_graph.add_screen(
            route=home_route,
            title=root_signature.title,
            source_module="root",
            status="discovered",
            metadata={
                "reason": "module_scope_entry",
                "scope": "module",
            },
        )
        current_node_id = home_route
        current_observation = observation

        for step in boundary.entry_steps:
            interaction = self.ui_event_explorer.interaction_executor.click(step.selector)
            if not interaction.success:
                raise RuntimeError(
                    "No se pudo reproducir la rama MODULE en "
                    f"{step.label!r}: {interaction.error}"
                )
            if self.ui_event_explorer.event_wait_ms:
                self.page.wait_for_timeout(self.ui_event_explorer.event_wait_ms)

            next_observation = self._observe_screen(title_hint=step.label)
            next_signature = next_observation.signature
            next_state_id = self.state_registry.build_state_id(
                next_signature.structural_fingerprint
            )
            event = UIEvent(
                event_type=UIEventType.EXPAND_MENU,
                label=step.label,
                selector=step.selector,
                decision=EventDecision.ALLOW,
                risk_level=RiskLevel.LOW,
                source="module_scope",
                reasons=("pinned_module_navigation",),
                metadata={
                    "target_module_id": boundary.root_module_id,
                    "navigation_depth": step.depth,
                },
            )
            source_path = current_state.path or CrawlPath(
                root_state_id=current_state.state_id
            )
            target_path = source_path.append(
                CrawlPathStep(
                    source_state_id=current_state.state_id,
                    event=event,
                    target_state_id=next_state_id,
                )
            )
            registration = self.state_registry.register_signature(
                signature=next_signature,
                path=target_path,
                metadata={
                    "kind": "ui_event_state",
                    "base_route": home_route,
                    "discovered_from": current_state.state_id,
                    "scope": "module",
                    "target_module_id": boundary.root_module_id,
                    "navigation_depth": step.depth,
                },
            )
            target_state = registration.state
            self.state_flow_graph.add_state(current_state)
            self.state_flow_graph.add_state(target_state)
            self.state_flow_graph.add_transition(
                Transition(
                    source_state_id=current_state.state_id,
                    target_state_id=target_state.state_id,
                    event=event,
                    changed_route=(current_state.route != target_state.route),
                    metadata={
                        "scope": "module",
                        "pinned_navigation": True,
                    },
                )
            )

            event_node_id = (
                f"{home_route}#state:{next_signature.structural_fingerprint[:12]}"
            )
            self.routes_graph.add_screen(
                route=event_node_id,
                title=next_signature.title or step.label,
                source_module=home_route,
                status="discovered",
                metadata={
                    "kind": "ui_state",
                    "state_id": target_state.state_id,
                    "base_route": home_route,
                    "scope": "module",
                    "target_module_id": boundary.root_module_id,
                    "path": target_state.path.to_dict() if target_state.path else None,
                },
            )
            self.routes_graph.add_transition(
                source=current_node_id,
                target=event_node_id,
                label=step.label,
                kind="ui_event",
                metadata={
                    "event_type": UIEventType.EXPAND_MENU.value,
                    "event_category": UIEventType.EXPAND_MENU.value,
                    "decision": EventDecision.ALLOW.value,
                    "risk_level": RiskLevel.LOW.value,
                    "selector": step.selector,
                    "scope": "module",
                    "pinned_navigation": True,
                },
            )
            self._register_module_event_discovered_links(
                source_route=event_node_id,
                source_screen_data=current_observation.screen_data,
                after_screen_data=next_observation.screen_data,
                target_state=target_state,
                event_category=UIEventType.EXPAND_MENU.value,
                depth=step.depth - 1,
            )
            current_observation = next_observation
            current_state = target_state
            current_node_id = event_node_id

        return current_state, current_node_id

    def crawl_screen(self, route: str) -> CrawlSummary:
        """Explora una sola ruta funcional y sus estados UI seguros.

        El alcance es exacto por pathname: los href descubiertos hacia otras
        pantallas se observan en la extracción, pero no entran a la frontera.
        Los artefactos siguen usando el storage configurado por el caller.
        """
        target = self.policy.normalize_href(route)
        if target is None:
            raise ValueError("La ruta objetivo no es válida")
        identity = self._route_identity(target)
        if self.route_scope is None:
            self.route_scope = {identity}
        elif identity not in self.route_scope:
            raise ValueError("La ruta objetivo está fuera del route_scope configurado")
        if not self._is_allowed_route(target):
            raise ValueError("La ruta objetivo no está permitida por la política")

        try:
            self._emit_progress("navigating_target", target=target)
            self.navigator.goto_path(target)
            if self.page_wait_ms:
                self.page.wait_for_timeout(self.page_wait_ms)
            actual_route = self.navigator.current_path()
            if not self._is_allowed_route(actual_route):
                raise RuntimeError("La ruta objetivo redirigió fuera del alcance permitido")
            self._capture_current_screen(
                source="screen_scope",
                depth=0,
                reason="screen_scope_target",
            )
            self._checkpoint_outputs()
            self._crawl_until_fixed_point()
        except KeyboardInterrupt:
            print("\nInterrupción detectada dentro del crawler de pantalla.")
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

    def _crawl_until_fixed_point(self) -> None:
        """Alterna rutas y estados UI hasta agotar ambas fronteras.

        Un estado UI puede revelar href que no existían durante el primer
        recorrido de rutas. Esas rutas deben volver a consumirse antes de
        considerar finalizado el descubrimiento.

        El límite ``max_pages_total`` sigue siendo una condición de parada:
        si se alcanza, las rutas restantes se conservan como pendientes en el
        resumen en lugar de provocar un ciclo sin progreso.
        """
        while True:
            can_process_routes = (
                self.frontier.has_pending()
                and self.frontier.visited_count() < self.max_pages_total
            )
            can_process_states = self.state_frontier.has_pending()

            if not can_process_routes and not can_process_states:
                break

            before = (
                self.frontier.visited_count(),
                self.frontier.pending_count(),
                self.state_frontier.explored_count(),
                self.state_frontier.pending_count(),
            )

            if can_process_routes:
                self._crawl_pending_targets()

            # La exploración de rutas puede haber creado nuevos estados.
            if self.state_frontier.has_pending():
                self._crawl_pending_states()

            self._emit_progress("exploring_fixed_point")
            after = (
                self.frontier.visited_count(),
                self.frontier.pending_count(),
                self.state_frontier.explored_count(),
                self.state_frontier.pending_count(),
            )

            if after == before:
                self._save_uncertainty(
                    route=self.navigator.current_path(),
                    reason="crawl_fixed_point_stalled",
                    extra={
                        "visited_routes": after[0],
                        "pending_routes": after[1],
                        "explored_states": after[2],
                        "pending_states": after[3],
                        "max_pages_total": self.max_pages_total,
                    },
                )
                break

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

            if not self._is_allowed_route(target.route):
                continue

            try:
                self.navigator.goto_path(target.route)

                if self.page_wait_ms:
                    self.page.wait_for_timeout(self.page_wait_ms)

                self._capture_current_screen(
                    source=target.source,
                    depth=target.depth,
                    reason=target.reason,
                    title_hint=target.title_hint,
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

    def _capture_current_screen(
        self,
        source: str,
        depth: int,
        reason: str,
        title_hint: str = "",
    ) -> None:
        observation = self._observe_screen(title_hint=title_hint)
        screen_data = observation.screen_data

        route = screen_data.get("path") or self.navigator.current_path()

        if not self._is_allowed_route(route):
            return

        if self.frontier.is_visited(route):
            return

        availability = self.screen_availability.classify(screen_data)
        screen_data["availability"] = availability.to_dict()

        if not availability.available:
            self.frontier.mark_visited(route)
            self._unavailable_routes.add(route)
            prefix = self._build_artifact_prefix(route)
            self._save_screen_artifacts(
                route=route,
                screen_data=screen_data,
                prefix=prefix,
                source=source,
                depth=depth,
                reason=reason,
                status=availability.status,
            )
            self.routes_graph.add_screen(
                route=route,
                title=(
                    screen_data.get("functional_title")
                    or title_hint
                    or screen_data.get("title", "")
                ),
                source_module=source,
                status=availability.status,
                metadata={
                    "reason": reason,
                    "depth": depth,
                    "availability": availability.to_dict(),
                    "title_source": screen_data.get("title_source", ""),
                    "title_confidence": screen_data.get("title_confidence", 0.0),
                },
            )
            self._save_uncertainty(
                route=route,
                reason="unavailable_screen",
                extra={
                    "availability_status": availability.status,
                    "matched_patterns": list(availability.matched_patterns),
                    "text_field": availability.text_field,
                    "source": source,
                    "depth": depth,
                    "artifacts": screen_data.get("artifacts", {}),
                },
            )
            self._checkpoint_outputs()
            self._emit_progress("screen_captured", current_route=route)
            return

        signature = observation.signature
        state_id = self.state_registry.build_state_id(
            signature.structural_fingerprint
        )
        root_path = CrawlPath(root_state_id=state_id)
        source_state = self.state_registry.register_signature(
            signature=signature,
            path=root_path,
            metadata={
                "source": source,
                "depth": depth,
                "reason": reason,
                "kind": "route_root_state",
                "title_hint": title_hint,
                "canonical_title": signature.title,
                "state_observation": observation.diagnostics(),
            },
        ).state
        self.state_flow_graph.add_state(source_state)

        screen_data["ui_state"] = source_state.to_dict()
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

        if self._should_explore_route_root(route):
            self._explore_ui_events_from_screen(
                route=route,
                screen_data=screen_data,
                source_state=source_state,
                depth=depth,
                allowed_categories=self._categories_for_state(source_state),
            )
        else:
            self.state_frontier.mark_explored(source_state.state_id)

        self._detect_and_store_uncertainty(
            route=route,
            screen_data=screen_data,
            discovered_links=discovered_links,
        )

        self._checkpoint_outputs()
        self._emit_progress("screen_captured", current_route=route)


    def _observe_screen(
        self,
        title_hint: str = "",
        canonical_title: str | None = None,
    ):
        observer = StableStateObserver(
            profile=self.profile,
            extractor=self.extractor,
            signature_builder=self.state_signature_builder,
            wait_fn=self.page.wait_for_timeout,
        )
        return observer.observe(
            title_hint=title_hint,
            canonical_title=canonical_title,
        )

    def _extract_screen(self, title_hint: str = "") -> dict[str, Any]:
        """Mantiene compatibilidad con extractores personalizados antiguos."""
        try:
            return self.extractor.extract(title_hint=title_hint)
        except TypeError as error:
            if "title_hint" not in str(error):
                raise
            return self.extractor.extract()

    def _save_screen_artifacts(
        self,
        route: str,
        screen_data: dict[str, Any],
        prefix: str,
        source: str,
        depth: int,
        reason: str,
        status: str = "discovered",
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
            "status": status,
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
            title=(
                screen_data.get("functional_title")
                or screen_data.get("title", "")
            ),
            source_module=source,
            status="discovered",
            metadata={
                "reason": reason,
                "depth": depth,
                "title_source": screen_data.get("title_source", ""),
                "title_confidence": screen_data.get("title_confidence", 0.0),
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
            region = link.get("region", "main_content")

            if target_route == source_route:
                continue

            if not self._is_allowed_route(target_route):
                continue

            already_known = self.routes_graph.has_screen(target_route)
            if only_new_targets and already_known:
                continue

            # El menú lateral se repite en casi todas las rutas. Conservar sus
            # enlaces desde la raíz o cuando descubren una ruta nueva, pero no
            # crear aristas cruzadas artificiales entre todas las pantallas.
            repeated_global_link = (
                region == "global_navigation"
                and source_route != self.home_route
                and "#state:" not in source_route
                and already_known
            )
            if repeated_global_link:
                continue

            self.routes_graph.add_screen(
                route=target_route,
                title=link.get("text", ""),
                source_module=source_route,
                status="discovered",
                metadata={
                    "discovered_from": source_route,
                    "title_source": "discovery_link",
                    "title_confidence": 0.90,
                    "region": region,
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
                    "region": region,
                },
            )

            self.frontier.push(
                CrawlTarget(
                    route=target_route,
                    source=source_route,
                    depth=depth + 1,
                    reason=reason,
                    title_hint=link.get("text", ""),
                )
            )

    def _explore_ui_events_from_screen(
        self,
        route: str,
        screen_data: dict[str, Any],
        source_state: UIState,
        depth: int,
        allowed_categories: set[str] | None = None,
    ) -> None:
        results = self.ui_event_explorer.explore_current_state(
            screen_data=screen_data,
            source_state=source_state,
            allowed_categories=allowed_categories,
        )

        changed_results = [
            result
            for result in results
            if result.changed
            and result.error is None
            and self._ui_event_result_in_scope(result, source_state)
        ]

        if not results:
            self.state_frontier.mark_explored(source_state.state_id)
            return

        for result in changed_results:
            target_signature = self.state_signature_builder.build(
                result.after_screen_data
            )
            target_state_id = self.state_registry.build_state_id(
                target_signature.structural_fingerprint
            )
            source_path = source_state.path or CrawlPath(
                root_state_id=source_state.state_id
            )
            target_path = source_path.append(
                CrawlPathStep(
                    source_state_id=source_state.state_id,
                    event=result.event,
                    target_state_id=target_state_id,
                )
            )
            registration = self.state_registry.register_signature(
                signature=target_signature,
                path=target_path,
                metadata={
                    "kind": "ui_event_state",
                    "base_route": route,
                    "discovered_from": source_state.state_id,
                    "candidate": result.candidate,
                },
            )
            target_state = registration.state
            result.target_state_id = target_state.state_id

            self.state_flow_graph.add_state(source_state)
            self.state_flow_graph.add_state(target_state)
            self.state_flow_graph.add_transition(
                Transition(
                    source_state_id=source_state.state_id,
                    target_state_id=target_state.state_id,
                    event=result.event,
                    changed_route=(
                        result.before_route != result.after_route
                    ),
                    metadata={
                        "candidate": result.candidate,
                        "restored_before": result.restored_before,
                        "restore_strategy": result.restore_strategy,
                    },
                )
            )

            self._persist_ui_event_state(
                route=route,
                depth=depth,
                source_state=source_state,
                target_state=target_state,
                source_screen_data=screen_data,
                result=result,
            )

            if self._should_queue_dynamic_state(registration.is_new, target_state):
                self.state_frontier.push_state(
                    target_state,
                    source_state_id=source_state.state_id,
                    reason="ui_event_state_discovered",
                )

        self.state_frontier.mark_explored(source_state.state_id)
        self._save_ui_event_results(
            route=route,
            source_state_id=source_state.state_id,
            results=[result.to_dict() for result in results],
        )

    def _persist_ui_event_state(
        self,
        route: str,
        depth: int,
        source_state: UIState,
        target_state: UIState,
        source_screen_data: dict[str, Any],
        result,
    ) -> None:
        event_prefix = self._build_ui_state_prefix(
            route=route,
            fingerprint=result.after_fingerprint,
        )
        after_screen_data = result.after_screen_data
        artifacts: dict[str, str] = {}

        if result.after_html is not None:
            html_path = self.storage.save_html_content(
                html=result.after_html,
                prefix=event_prefix,
            )
            artifacts["html"] = str(html_path)

        if result.after_screenshot is not None:
            screenshot_path = self.storage.save_screenshot_bytes(
                content=result.after_screenshot,
                prefix=event_prefix,
            )
            artifacts["screenshot"] = str(screenshot_path)

        after_screen_data["artifacts"] = artifacts
        after_screen_data["ui_state"] = target_state.to_dict()
        after_screen_data["crawler"] = {
            "route": after_screen_data.get("path") or route,
            "source": route,
            "depth": depth,
            "event_depth": (
                target_state.path.depth if target_state.path else 0
            ),
            "reason": "ui_event_state_change",
            "status": "discovered",
            "source_state_id": source_state.state_id,
            "target_state_id": target_state.state_id,
            "ui_event_candidate": result.candidate,
            "before_fingerprint": result.before_fingerprint,
            "after_fingerprint": result.after_fingerprint,
            "restored_before": result.restored_before,
            "restore_strategy": result.restore_strategy,
            "artifact_error": result.artifact_error,
        }

        raw_json_path = self.storage.save_raw_screen_json(
            data=after_screen_data,
            prefix=event_prefix,
        )
        after_screen_data["artifacts"]["raw_json"] = str(raw_json_path)

        if result.artifact_error:
            self._save_uncertainty(
                route=route,
                reason="ui_event_artifact_capture_error",
                extra={
                    "source_state_id": source_state.state_id,
                    "target_state_id": target_state.state_id,
                    "candidate": result.candidate,
                    "error": result.artifact_error,
                },
            )

        # Se conserva el identificador legado en routes_graph para no romper
        # consumidores actuales. El state-flow graph usa el ID canónico.
        event_node_id = f"{route}#state:{result.after_fingerprint[:12]}"

        self.routes_graph.add_screen(
            route=event_node_id,
            title=after_screen_data.get("title", ""),
            source_module=route,
            status="discovered",
            metadata={
                "kind": "ui_state",
                "state_id": target_state.state_id,
                "base_route": route,
                "before_fingerprint": result.before_fingerprint,
                "after_fingerprint": result.after_fingerprint,
                "candidate": result.candidate,
                "path": (
                    target_state.path.to_dict()
                    if target_state.path
                    else None
                ),
            },
        )

        self.routes_graph.add_transition(
            source=route,
            target=event_node_id,
            label=result.candidate.get("label", ""),
            kind="ui_event",
            metadata={
                "state_id": target_state.state_id,
                "event_type": result.candidate.get("event_type"),
                "action_kind": result.candidate.get("action_kind"),
                "event_category": result.candidate.get("event_category"),
                "decision": result.candidate.get("decision"),
                "risk_level": result.candidate.get("risk_level"),
                "selector": result.candidate.get("selector"),
            },
        )

        self._register_ui_event_discovered_links(
            source_route=event_node_id,
            source_screen_data=source_screen_data,
            after_screen_data=after_screen_data,
            target_state=target_state,
            result=result,
            depth=depth,
        )

    def _register_ui_event_discovered_links(
        self,
        *,
        source_route: str,
        source_screen_data: dict[str, Any],
        after_screen_data: dict[str, Any],
        target_state: UIState,
        result,
        depth: int,
    ) -> None:
        after_links = self.discovery.discover_allowed_links(after_screen_data)
        if getattr(self, "module_boundary", None) is None:
            self._register_discovered_links(
                source_route=source_route,
                links=after_links,
                depth=depth,
                reason="ui_event_discovered_href",
                only_new_targets=True,
            )
            return

        event_category = str(
            result.candidate.get("event_category")
            or result.candidate.get("event_type")
            or result.event.event_type.value
        ).strip()
        self._register_module_event_discovered_links(
            source_route=source_route,
            source_screen_data=source_screen_data,
            after_screen_data=after_screen_data,
            target_state=target_state,
            event_category=event_category,
            depth=depth,
        )

    def _register_module_event_discovered_links(
        self,
        *,
        source_route: str,
        source_screen_data: dict[str, Any],
        after_screen_data: dict[str, Any],
        target_state: UIState,
        event_category: str,
        depth: int,
    ) -> None:
        """Registra únicamente href revelados causalmente dentro del MODULE.

        Las rutas conocidas pueden haber sido capturadas antes de explorar los
        estados del menú. Aun así, la arista estado->pantalla debe conservarse
        porque es la evidencia estructural que usa el Canonical Builder para
        reconstruir ownership y jerarquía. Por eso aquí no se descarta una
        arista solo porque el nodo target ya exista.
        """
        if getattr(self, "module_boundary", None) is None:
            return

        before_routes = {
            self._route_identity(link["route"])
            for link in self.discovery.discover_allowed_links(source_screen_data)
        }
        menu_selectors = self._menu_selectors_from_path(target_state.path)
        if not self.module_boundary.is_inside_selected_branch(menu_selectors):
            return

        admitted_links: list[dict[str, Any]] = []
        for link in self.discovery.discover_allowed_links(after_screen_data):
            route = link.get("route")
            identity = self._route_identity(route) if route else ""
            newly_revealed = bool(identity and identity not in before_routes)
            if not newly_revealed:
                continue
            if not self.module_boundary.allows_discovered_route(
                route,
                menu_selectors=menu_selectors,
                event_category=event_category,
                newly_revealed=True,
            ):
                continue
            if not self.policy.is_allowed_route(route):
                continue

            if self.route_scope is None:
                self.route_scope = set()
            if identity not in self.route_scope:
                self.route_scope.add(identity)
                self._module_dynamic_routes.add(identity)
            admitted_links.append(link)

        self._register_discovered_links(
            source_route=source_route,
            links=admitted_links,
            depth=depth,
            reason="module_scope_discovered_href",
            # Preserve provenance even when a known route node was already
            # captured earlier in this MODULE run. Frontier de-duplicates the
            # route, while RoutesGraphBuilder de-duplicates the relation.
            only_new_targets=False,
        )

    def _ui_event_result_in_scope(self, result, source_state: UIState) -> bool:
        if not result.after_route or not self.policy.is_allowed_route(result.after_route):
            return False
        if self._is_route_in_scope(result.after_route):
            return True
        if getattr(self, "module_boundary", None) is None:
            return False

        event_category = str(
            result.candidate.get("event_category")
            or result.candidate.get("event_type")
            or result.event.event_type.value
        ).strip()
        if event_category != "expand_menu":
            return False

        selectors = list(self._menu_selectors_from_path(source_state.path))
        if result.event.selector:
            selectors.append(result.event.selector)
        return self.module_boundary.is_inside_selected_branch(selectors)

    def _module_state_inside_branch(self, state: UIState) -> bool:
        if getattr(self, "module_boundary", None) is None:
            return False
        return self.module_boundary.is_inside_selected_branch(
            self._menu_selectors_from_path(state.path)
        )

    @staticmethod
    def _menu_selectors_from_path(path: CrawlPath | None) -> tuple[str, ...]:
        if path is None:
            return ()
        return tuple(
            step.event.selector
            for step in path.steps
            if step.event.selector
            and step.event.event_type.value in {"expand_menu", "collapse_menu"}
        )

    def _state_event_depth(self, path: CrawlPath) -> int:
        if getattr(self, "module_boundary", None) is None:
            return path.depth
        return max(0, path.depth - getattr(self, "_module_entry_depth", 0))

    def _save_ui_event_results(
        self,
        route: str,
        source_state_id: str,
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
                    "before_exact_fingerprint": result.get("before_exact_fingerprint"),
                    "after_exact_fingerprint": result.get("after_exact_fingerprint"),
                    "before_route": result.get("before_route"),
                    "after_route": result.get("after_route"),
                    "error": result.get("error"),
                    "source_state_id": result.get("source_state_id"),
                    "target_state_id": result.get("target_state_id"),
                    "restored_before": result.get("restored_before"),
                    "restore_strategy": result.get("restore_strategy"),
                    "restore_error": result.get("restore_error"),
                    "restore_diagnostics": result.get("restore_diagnostics", {}),
                    "after_observation": result.get("after_observation", {}),
                    "interaction_attempts": result.get("interaction_attempts", 0),
                    "interaction_strategy": result.get("interaction_strategy"),
                    "interaction_succeeded": result.get("interaction_succeeded", False),
                    "outcome": result.get("outcome"),
                    "artifact_error": result.get("artifact_error"),
                    "after_summary": {
                        "title": (
                            after_screen_data.get("functional_title")
                            or after_screen_data.get("title")
                        ),
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

        outcome_counts: dict[str, int] = {}
        for result in slim_results:
            outcome = str(result.get("outcome") or "unknown")
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

        payload = {
            "route": route,
            "source_state_id": source_state_id,
            "status": "ui_events_explored",
            "results_count": len(slim_results),
            "outcomes": outcome_counts,
            "results": slim_results,
        }

        self.storage.save_uncertainty_json(
            data=payload,
            prefix=(
                f"{route}_ui_events_"
                f"{safe_slug(source_state_id, fallback='state')[-24:]}"
            ),
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

        event_candidates = self.candidate_discovery.discover_candidates(screen_data)
        denied_candidates = [
            candidate for candidate in event_candidates
            if candidate.decision == "deny"
        ]
        review_candidates = [
            candidate for candidate in event_candidates
            if candidate.decision == "review"
        ]

        dangerous_buttons = [
            button
            for button in buttons
            if self.policy.is_dangerous_action_label(button.get("text"))
        ]

        if dangerous_buttons or denied_candidates:
            reasons.append(
                "La pantalla contiene acciones bloqueadas por la política de seguridad."
            )

        if review_candidates:
            reasons.append(
                "La pantalla contiene acciones ambiguas pendientes de revisión humana."
            )

        if inputs and buttons and not discovered_links:
            reasons.append(
                "La pantalla parece depender de formularios o búsqueda "
                "para mostrar nuevos estados."
            )

        if not reasons:
            return

        self._save_uncertainty(
            route=route,
            reason="uncertain_screen",
            extra={
                "reasons": reasons,
                "title": (
                    screen_data.get("functional_title")
                    or screen_data.get("title", "")
                ),
                "url": screen_data.get("url", ""),
                "buttons": buttons,
                "inputs": inputs,
                "custom_interactives": custom_interactives,
                "event_policy": {
                    "denied": [candidate.to_dict() for candidate in denied_candidates],
                    "review": [candidate.to_dict() for candidate in review_candidates],
                },
                "artifacts": screen_data.get("artifacts", {}),
                "next_step": "Revisión humana; luego LLM helper podrá proponer reglas YAML.",
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

    def _should_explore_route_root(self, route: str) -> bool:
        if not self.ui_events_enabled:
            return False
        if route == self.home_route:
            return self.home_navigation_enabled
        return self.explore_local_route_roots and self.max_event_depth >= 1

    def _categories_for_state(self, state: UIState) -> set[str]:
        is_home_root = (
            state.route == self.home_route
            and (state.path is None or state.path.depth == 0)
        )
        if is_home_root:
            return set(self.home_event_categories)

        if (
            getattr(self, "module_boundary", None) is not None
            and self._route_identity(state.route) == self._route_identity(self.home_route)
            and self._module_state_inside_branch(state)
        ):
            # Los estados del menú dentro del MODULE seleccionado continúan
            # descubriendo solamente expand_menu descendientes. Los estados de
            # pantallas funcionales conservan las categorías locales normales.
            return set(self.home_event_categories)

        return set(self.local_event_categories)

    def _should_queue_dynamic_state(
        self,
        is_new: bool,
        state: UIState,
    ) -> bool:
        if not is_new or not self.recursive_state_exploration:
            return False
        if state.path is None:
            return False
        return self._state_event_depth(state.path) < self.max_event_depth

    def _crawl_pending_states(self) -> None:
        """Explora estados reproducibles hasta la profundidad permitida."""
        while self.state_frontier.has_pending():
            target = self.state_frontier.pop()
            if target is None:
                break

            if self._state_event_depth(target.path) >= self.max_event_depth:
                self.state_frontier.mark_explored(target.state_id)
                continue

            try:
                state = self.state_registry.require(target.state_id)
                restored = self.state_restorer.restore(state)
                if not restored.success:
                    self.state_frontier.mark_explored(state.state_id)
                    self._save_uncertainty(
                        route=state.route,
                        reason="dynamic_state_restore_failed",
                        extra={
                            "state_id": state.state_id,
                            "event_depth": target.depth,
                            "source_state_id": target.source_state_id,
                            "error": restored.error,
                            "strategy": restored.strategy,
                        },
                    )
                    continue

                self._explore_ui_events_from_screen(
                    route=state.route,
                    screen_data=restored.screen_data,
                    source_state=state,
                    depth=target.depth,
                    allowed_categories=self._categories_for_state(state),
                )
                self._checkpoint_outputs()

            except Exception as error:
                self.state_frontier.mark_explored(target.state_id)
                registered_state = self.state_registry.get(target.state_id)
                self._save_uncertainty(
                    route=(registered_state.route if registered_state else ""),
                    reason="dynamic_state_exploration_error",
                    extra={
                        "state_id": target.state_id,
                        "event_depth": target.depth,
                        "source_state_id": target.source_state_id,
                        "error": str(error),
                    },
                )

    def _state_exploration_summary(self) -> dict[str, Any]:
        return {
            "max_event_depth": self.max_event_depth,
            "home_navigation_enabled": self.home_navigation_enabled,
            "explore_local_route_roots": self.explore_local_route_roots,
            "recursive_state_exploration": self.recursive_state_exploration,
            "home_event_categories": sorted(self.home_event_categories),
            "local_event_categories": sorted(self.local_event_categories),
            "frontier_pending_count": self.state_frontier.pending_count(),
            "frontier_explored_count": self.state_frontier.explored_count(),
        }

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

        self.storage.save_processed_structural_json(
            data=self.state_registry.to_dict(),
            filename="state_registry.partial.json",
        )

        self.storage.save_processed_structural_json(
            data=self.state_flow_graph.to_dict(),
            filename="state_flow_graph.partial.json",
        )

        self.storage.save_processed_structural_json(
            data=self._state_exploration_summary(),
            filename="state_exploration_summary.partial.json",
        )

    @staticmethod
    def _route_identity(route: str) -> str:
        value = str(route or "").split("#", 1)[0].split("?", 1)[0].strip()
        if len(value) > 1:
            value = value.rstrip("/")
        return value or "/"

    def _is_route_in_scope(self, route: str | None) -> bool:
        if self.route_scope is None:
            return True
        if not route:
            return False
        return self._route_identity(route) in self.route_scope

    def _is_allowed_route(self, route: str | None) -> bool:
        return bool(
            route
            and self.policy.is_allowed_route(route)
            and self._is_route_in_scope(route)
        )

    def _emit_progress(self, stage: str, **extra: Any) -> None:
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        payload = {
            "routes_visited": self.frontier.visited_count(),
            "routes_pending": self.frontier.pending_count(),
            "states_explored": self.state_frontier.explored_count(),
            "states_pending": self.state_frontier.pending_count(),
            "functional_screens": self.screen_index.screen_count(),
            "unavailable_routes": len(self._unavailable_routes),
            "structural_nodes": self.routes_graph.node_count(),
            "structural_relationships": self.routes_graph.edge_count(),
            "ui_states": self.state_flow_graph.state_count(),
            "ui_transitions": self.state_flow_graph.transition_count(),
            **extra,
        }
        payload["work_units"] = (
            payload["routes_visited"] + payload["states_explored"]
        )
        callback(stage, payload)

    def _save_outputs(self) -> CrawlSummary:
        routes_graph_data = self.routes_graph.to_dict()
        screen_index_data = self.screen_index.to_dict()
        state_registry_data = self.state_registry.to_dict()
        state_flow_graph_data = self.state_flow_graph.to_dict()

        routes_graph_path = self.storage.save_processed_structural_json(
            data=routes_graph_data,
            filename="routes_graph.json",
        )

        screen_index_path = self.storage.save_processed_structural_json(
            data=screen_index_data,
            filename="screen_index.json",
        )

        self.storage.save_processed_structural_json(
            data=build_event_policy_audit(self.profile, screen_index_data),
            filename="event_policy_audit.json",
        )

        self.storage.save_processed_structural_json(
            data=state_registry_data,
            filename="state_registry.json",
        )

        state_flow_graph_path = self.storage.save_processed_structural_json(
            data=state_flow_graph_data,
            filename="state_flow_graph.json",
        )

        self.storage.save_processed_structural_json(
            data=self._state_exploration_summary(),
            filename="state_exploration_summary.json",
        )

        self._emit_progress("saving_outputs")

        return CrawlSummary(
            visited_count=self.frontier.visited_count(),
            pending_count=self.frontier.pending_count(),
            nodes_count=self.routes_graph.node_count(),
            edges_count=self.routes_graph.edge_count(),
            routes_graph_path=str(routes_graph_path),
            screen_index_path=str(screen_index_path),
            states_count=self.state_flow_graph.state_count(),
            state_transitions_count=self.state_flow_graph.transition_count(),
            state_flow_graph_path=str(state_flow_graph_path),
            state_frontier_pending_count=self.state_frontier.pending_count(),
            state_frontier_explored_count=self.state_frontier.explored_count(),
            functional_screen_count=self.screen_index.screen_count(),
            unavailable_count=len(self._unavailable_routes),
        )

    def _build_artifact_prefix(self, route: str) -> str:
        return safe_slug(route, fallback="screen")

    def _build_ui_state_prefix(self, route: str, fingerprint: str) -> str:
        route_slug = safe_slug(route, fallback="screen")
        return f"{route_slug}_state_{fingerprint[:12]}"