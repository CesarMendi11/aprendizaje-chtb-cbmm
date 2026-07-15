from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page

from src.browser.navigator import ERPNavigator
from src.crawler.frontier import CrawlTarget, Frontier
from src.crawler.path_replayer import PathReplayer
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
            self._crawl_pending_states()

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

        if not self.policy.is_allowed_route(route):
            return

        if self.frontier.is_visited(route):
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

            if not self.policy.is_allowed_route(target_route):
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
            if result.changed and result.error is None
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

        discovered_links = self.discovery.discover_allowed_links(
            after_screen_data
        )
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
        return state.path.depth < self.max_event_depth

    def _crawl_pending_states(self) -> None:
        """Explora estados reproducibles hasta la profundidad permitida."""
        while self.state_frontier.has_pending():
            target = self.state_frontier.pop()
            if target is None:
                break

            if target.depth >= self.max_event_depth:
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
        )

    def _build_artifact_prefix(self, route: str) -> str:
        return safe_slug(route, fallback="screen")

    def _build_ui_state_prefix(self, route: str, fingerprint: str) -> str:
        route_slug = safe_slug(route, fallback="screen")
        return f"{route_slug}_state_{fingerprint[:12]}"