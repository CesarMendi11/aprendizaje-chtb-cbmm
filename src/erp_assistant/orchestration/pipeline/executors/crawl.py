from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from erp_assistant.config.paths import PROJECT_ROOT

from playwright.sync_api import sync_playwright

from erp_assistant.acquisition.browser.navigator import ERPNavigator
from erp_assistant.config.pipeline_settings import PipelineSettings
from erp_assistant.config.profile_loader import ProfileLoader
from erp_assistant.acquisition.crawling.module_scope import ModuleCrawlBoundary, ModuleCrawlBoundaryError
from erp_assistant.acquisition.crawling.route_crawler import CrawlSummary, RouteCrawler
from erp_assistant.persistence.postgres.enums import PipelineJobScope
from erp_assistant.acquisition.policy.route_policy import RoutePolicy

ProgressCallback = Callable[[str, dict[str, Any]], None]


class CrawlJobExecutionError(RuntimeError):
    pass


def _project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _relative_project_path(value: str | Path) -> str:
    path = Path(value)
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def _isolated_profile(profile: dict[str, Any], run_root: Path) -> dict[str, Any]:
    """Clone a profile and redirect every crawler output to one isolated run."""
    isolated = deepcopy(profile)
    output_root = run_root.resolve()
    isolated["output"] = {
        "raw_playwright_dir": str(output_root / "raw" / "playwright"),
        "html_dir": str(output_root / "raw" / "html"),
        "screenshots_dir": str(output_root / "raw" / "screenshots"),
        "marked_screenshots_dir": str(output_root / "raw" / "marked_screenshots"),
        "processed_structural_dir": str(output_root / "processed" / "structural"),
        "processed_semantic_dir": str(output_root / "processed" / "semantic"),
        "review_structural_dir": str(output_root / "review" / "structural"),
        "review_semantic_dir": str(output_root / "review" / "semantic"),
        "approved_neo4j_dir": str(output_root / "approved" / "neo4j"),
        "approved_chromadb_dir": str(output_root / "approved" / "chromadb"),
        "rejected_dir": str(output_root / "rejected"),
        "cache_dir": str(output_root / "cache"),
    }
    return isolated


class CrawlJobExecutor:
    """Execute one controlled Playwright crawl without touching official snapshots."""

    def __init__(
        self,
        *,
        profile_path: str | Path | None = None,
        runs_root: str | Path | None = None,
    ):
        pipeline_settings = PipelineSettings()
        configured_profile = profile_path or pipeline_settings.crawl_profile_path
        configured_runs = runs_root or pipeline_settings.runs_root
        self.profile_path = _project_path(configured_profile)
        self.runs_root = _project_path(configured_runs)

    @property
    def profile_name(self) -> str:
        return self.profile_path.stem

    def execute(
        self,
        *,
        job_id: uuid.UUID | str,
        scope: PipelineJobScope | str,
        target: str | None,
        parameters: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        normalized_scope = PipelineJobScope(scope)
        if normalized_scope not in {
            PipelineJobScope.FULL,
            PipelineJobScope.MODULE,
            PipelineJobScope.SCREEN,
        }:
            raise CrawlJobExecutionError(
                "El runner de crawler sólo admite scope=full, scope=module o scope=screen"
            )

        emit = progress or (lambda _stage, _payload: None)
        emit("loading_profile", {"scope": normalized_scope.value, "target": target})

        loaded_profile = ProfileLoader(self.profile_path).load_with_provenance()
        profile = loaded_profile.profile
        source_profile = _relative_project_path(self.profile_path)
        target_value = self._validate_target(profile, normalized_scope, target)
        params = dict(parameters or {})
        module_boundary = self._module_boundary(
            normalized_scope,
            target_value,
            params,
        )
        run_root = self.runs_root / str(job_id)
        profile = _isolated_profile(profile, run_root)

        browser_config = profile.get("browser", {})
        headless = bool(params.get("headless", browser_config.get("headless", False)))
        slow_mo = int(params.get("slow_mo", browser_config.get("slow_mo", 0)) or 0)
        if slow_mo < 0 or slow_mo > 5000:
            raise CrawlJobExecutionError("slow_mo debe estar entre 0 y 5000 ms")

        viewport = browser_config.get("viewport", {"width": 1366, "height": 768})
        emit(
            "launching_browser",
            {
                "scope": normalized_scope.value,
                "target": target_value,
                "headless": headless,
                "slow_mo": slow_mo,
                "artifact_root": _relative_project_path(run_root),
            },
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless, slow_mo=slow_mo)
            context = browser.new_context(viewport=viewport, ignore_https_errors=True)
            page = context.new_page()

            try:
                navigator = ERPNavigator(page, profile)
                emit("logging_in", {"scope": normalized_scope.value, "target": target_value})
                navigator.login()
                emit(
                    "login_succeeded",
                    {
                        "scope": normalized_scope.value,
                        "target": target_value,
                        "current_route": navigator.current_path(),
                    },
                )

                route_scope = None
                if normalized_scope == PipelineJobScope.SCREEN:
                    route_scope = {target_value}
                elif module_boundary is not None:
                    route_scope = set(module_boundary.known_screen_routes)

                crawler = RouteCrawler(
                    page,
                    profile,
                    route_scope=route_scope,
                    progress_callback=emit,
                )
                if normalized_scope == PipelineJobScope.SCREEN:
                    summary = crawler.crawl_screen(target_value)
                elif normalized_scope == PipelineJobScope.MODULE:
                    assert module_boundary is not None
                    summary = crawler.crawl_module(module_boundary)
                else:
                    summary = crawler.crawl()
                return self._result(
                    summary,
                    run_root,
                    normalized_scope,
                    target_value,
                    profile_path=source_profile,
                    profile_sha256=loaded_profile.sha256,
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

    def _validate_target(
        self,
        profile: dict[str, Any],
        scope: PipelineJobScope,
        target: str | None,
    ) -> str | None:
        if scope == PipelineJobScope.FULL:
            if target:
                raise CrawlJobExecutionError("scope=full no acepta target")
            return None

        clean = (target or "").strip()
        if scope == PipelineJobScope.MODULE:
            if not clean.startswith("module:"):
                raise CrawlJobExecutionError(
                    "scope=module requiere un target_module_id canónico"
                )
            return clean

        if not clean or not clean.startswith("/") or "://" in clean:
            raise CrawlJobExecutionError("scope=screen requiere una ruta interna válida")
        normalized = RoutePolicy(profile).normalize_href(clean)
        if normalized is None or not RoutePolicy(profile).is_allowed_route(normalized):
            raise CrawlJobExecutionError("La ruta objetivo no está permitida por el perfil")
        return normalized


    @staticmethod
    def _module_boundary(
        scope: PipelineJobScope,
        target: str | None,
        parameters: dict[str, Any],
    ) -> ModuleCrawlBoundary | None:
        if scope != PipelineJobScope.MODULE:
            return None

        try:
            boundary = ModuleCrawlBoundary.from_payload(parameters.get("module_scope"))
        except ModuleCrawlBoundaryError as exc:
            raise CrawlJobExecutionError(
                f"El scope MODULE fijado es inválido: {exc}"
            ) from exc

        target_module_id = str(parameters.get("target_module_id") or "").strip()
        if target != boundary.root_module_id or target_module_id != boundary.root_module_id:
            raise CrawlJobExecutionError(
                "El job MODULE no conserva un target_module_id consistente con module_scope"
            )
        return boundary

    @staticmethod
    def _result(
        summary: CrawlSummary,
        run_root: Path,
        scope: PipelineJobScope,
        target: str | None,
        *,
        profile_path: str,
        profile_sha256: str,
    ) -> dict[str, Any]:
        return {
            "run_id": run_root.name,
            "scope": scope.value,
            "target": target,
            "artifact_root": _relative_project_path(run_root),
            "profile_path": profile_path,
            "profile_sha256": profile_sha256,
            "visited_routes": summary.visited_count,
            "pending_routes": summary.pending_count,
            "functional_screens": summary.functional_screen_count,
            "unavailable_routes": summary.unavailable_count,
            "structural_nodes": summary.nodes_count,
            "structural_relationships": summary.edges_count,
            "ui_states": summary.states_count,
            "ui_transitions": summary.state_transitions_count,
            "states_explored": summary.state_frontier_explored_count,
            "states_pending": summary.state_frontier_pending_count,
            "routes_graph_path": _relative_project_path(summary.routes_graph_path),
            "screen_index_path": _relative_project_path(summary.screen_index_path),
            "state_flow_graph_path": _relative_project_path(summary.state_flow_graph_path),
            "network_evidence": summary.network_evidence_count,
            "network_evidence_path": (
                _relative_project_path(summary.network_evidence_path)
                if summary.network_evidence_path
                else ""
            ),
        }
