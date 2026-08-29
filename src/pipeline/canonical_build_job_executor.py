from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from src.config.pipeline_settings import PipelineSettings
from src.config.profile_loader import ProfileLoader
from src.knowledge.crawl_execution_quality import (
    CrawlExecutionQualityError,
    build_crawl_execution_quality,
)
from src.database.enums import PipelineJobScope
from src.knowledge.canonical import (
    ArtifactLoadError,
    CanonicalKnowledgeBuilder,
    CanonicalKnowledgeExporter,
    CanonicalKnowledgeValidator,
    CanonicalNetworkEvidenceError,
    CanonicalNetworkEvidenceIntegrator,
    CanonicalSnapshotContext,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ProgressCallback = Callable[[str, dict[str, Any]], None]


class CanonicalBuildJobExecutionError(RuntimeError):
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


class CanonicalBuildJobExecutor:
    """Build canonical knowledge from one isolated crawler run.

    The executor never writes to the official ``data/processed/canonical`` directory.
    Its output stays under the source crawl run so an isolated crawl cannot replace
    governed knowledge without passing the downstream trust boundaries.
    """

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
        params = dict(parameters or {})
        source_raw = params.get("source_crawl_job_id")
        try:
            source_job_id = uuid.UUID(str(source_raw))
        except (TypeError, ValueError) as exc:
            raise CanonicalBuildJobExecutionError(
                "canonical_build requiere source_crawl_job_id válido"
            ) from exc

        expected_profile_path, expected_profile_sha256 = self._source_profile_pin(
            params
        )

        emit = progress or (lambda _stage, _payload: None)
        run_root = self.runs_root / str(source_job_id)
        structural_dir = run_root / "processed" / "structural"
        canonical_dir = run_root / "processed" / "canonical"

        emit(
            "loading_crawl_artifacts",
            {
                "work_units": 1,
                "progress_total": 4,
                "source_crawl_job_id": str(source_job_id),
                "source_scope": normalized_scope.value,
                "target": target,
            },
        )
        if not (structural_dir / "screen_index.json").is_file():
            raise CanonicalBuildJobExecutionError(
                "El crawl fuente no contiene screen_index.json final"
            )

        try:
            execution_quality = build_crawl_execution_quality(
                review_dir=run_root / "review" / "structural",
                structural_dir=structural_dir,
                source_crawl_result=params.get("source_crawl_result"),
                expected_run_id=str(source_job_id),
                expected_scope=normalized_scope.value,
                expected_target=target,
            )
        except CrawlExecutionQualityError as exc:
            raise CanonicalBuildJobExecutionError(str(exc)) from exc
        if not execution_quality["gate_passed"]:
            raise CanonicalBuildJobExecutionError(
                "El crawl fuente no supera el gate de calidad estructural: "
                f"{execution_quality['blocking_failures']} fallo(s) bloqueante(s); "
                f"state_restore_failures={execution_quality['state_restore_failures']}, "
                f"dynamic_state_exploration_errors="
                f"{execution_quality['dynamic_state_exploration_errors']}, "
                f"navigation_errors={execution_quality['navigation_errors']}, "
                f"fixed_point_stalls={execution_quality['fixed_point_stalls']}, "
                f"pending_routes={execution_quality['route_frontier_pending']}, "
                f"pending_states={execution_quality['state_frontier_pending']}."
            )

        actual_profile_path, loaded_profile = self._load_pinned_profile(
            expected_profile_path,
            expected_profile_sha256,
        )

        emit(
            "building_canonical",
            {
                "work_units": 2,
                "progress_total": 4,
                "source_crawl_job_id": str(source_job_id),
            },
        )
        snapshot_context = self._snapshot_context(
            normalized_scope,
            target,
            params,
        )

        builder = CanonicalKnowledgeBuilder(PROJECT_ROOT)
        try:
            knowledge = builder.build_from_paths(
                self.profile_path,
                structural_dir=structural_dir,
                profile=loaded_profile.profile,
                profile_sha256=loaded_profile.sha256,
            )
        except (ArtifactLoadError, OSError, ValueError) as exc:
            raise CanonicalBuildJobExecutionError(str(exc)) from exc

        try:
            network_result = CanonicalNetworkEvidenceIntegrator(
                PROJECT_ROOT
            ).integrate(
                knowledge,
                structural_dir / "network_evidence.json",
            )
        except CanonicalNetworkEvidenceError as exc:
            raise CanonicalBuildJobExecutionError(str(exc)) from exc
        knowledge = network_result.knowledge

        emit(
            "validating_canonical",
            {
                "work_units": 3,
                "progress_total": 4,
                "knowledge_version": knowledge.knowledge_version,
            },
        )
        issues = CanonicalKnowledgeValidator().validate(knowledge)
        errors = [item for item in issues if item.severity == "error"]
        if errors:
            raise CanonicalBuildJobExecutionError(
                f"Conocimiento canónico inválido: {len(errors)} errores"
            )
        if snapshot_context.erp_id and snapshot_context.erp_id != knowledge.erp_system.id:
            raise CanonicalBuildJobExecutionError(
                "El canonical parcial pertenece a un ERP distinto del crawl fijado"
            )
        if snapshot_context.scope == "module" and snapshot_context.target_module_id not in {
            module.id for module in knowledge.modules
        }:
            raise CanonicalBuildJobExecutionError(
                "El canonical MODULE no reconstruyó el módulo objetivo fijado"
            )
        if snapshot_context.scope == "screen":
            target_screen = next(
                (
                    screen
                    for screen in knowledge.screens
                    if screen.id == snapshot_context.target_screen_id
                ),
                None,
            )
            if target_screen is None or target_screen.route != snapshot_context.target:
                raise CanonicalBuildJobExecutionError(
                    "El canonical SCREEN no reconstruyó la pantalla objetivo fijada"
                )

        report = builder.build_report(knowledge, issues)
        report["network_evidence"] = network_result.report()
        report["sensitive_regions_excluded"] += (
            network_result.sensitive_exclusions
        )
        if network_result.omitted_observations:
            omitted = dict(report.get("omitted_entities") or {})
            omitted["network_evidence"] = (
                omitted.get("network_evidence", 0)
                + network_result.omitted_observations
            )
            report["omitted_entities"] = omitted
        report["snapshot"] = snapshot_context.model_dump(mode="json")
        report["crawl_execution_quality"] = execution_quality

        emit(
            "exporting_canonical",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": knowledge.knowledge_version,
            },
        )
        CanonicalKnowledgeExporter().export(
            knowledge,
            canonical_dir,
            pretty=True,
            build_report=report,
            snapshot_context=snapshot_context,
            manifest_metadata={"crawl_execution_quality": execution_quality},
        )

        return {
            "source_crawl_job_id": str(source_job_id),
            "profile_path": actual_profile_path,
            "profile_sha256": loaded_profile.sha256,
            "scope": normalized_scope.value,
            "target": target,
            "artifact_root": _relative_project_path(run_root),
            "canonical_dir": _relative_project_path(canonical_dir),
            "knowledge_path": _relative_project_path(canonical_dir / "knowledge.json"),
            "manifest_path": _relative_project_path(canonical_dir / "manifest.json"),
            "build_report_path": _relative_project_path(canonical_dir / "build_report.json"),
            "knowledge_version": knowledge.knowledge_version,
            "statistics": dict(knowledge.statistics),
            "warnings": len(knowledge.build_warnings),
            "validation_errors": 0,
            "network_evidence": network_result.observation_count,
            "network_evidence_screens": network_result.screen_count,
            "snapshot_mode": snapshot_context.mode,
            "snapshot_scope": snapshot_context.scope,
            "snapshot_target": snapshot_context.target,
            "target_module_id": snapshot_context.target_module_id,
            "target_screen_id": snapshot_context.target_screen_id,
            "base_knowledge_version_id": snapshot_context.base_knowledge_version_id,
            "base_knowledge_version": snapshot_context.base_knowledge_version,
            "crawl_execution_quality": execution_quality,
        }

    @staticmethod
    def _source_profile_pin(parameters: dict[str, Any]) -> tuple[str, str]:
        source_crawl_result = parameters.get("source_crawl_result")
        if not isinstance(source_crawl_result, dict):
            raise CanonicalBuildJobExecutionError(
                "El crawl fuente no conserva provenance de perfil utilizable"
            )
        profile_path = str(source_crawl_result.get("profile_path") or "").strip()
        profile_sha256 = str(
            source_crawl_result.get("profile_sha256") or ""
        ).strip().lower()
        if not profile_path or len(profile_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in profile_sha256
        ):
            raise CanonicalBuildJobExecutionError(
                "El crawl fuente no conserva profile_path/profile_sha256 fijados"
            )
        return profile_path, profile_sha256

    def _load_pinned_profile(self, expected_path: str, expected_sha256: str):
        if not self.profile_path.is_file():
            raise CanonicalBuildJobExecutionError("Perfil de crawler no encontrado")
        actual_path = _relative_project_path(self.profile_path)
        if actual_path != expected_path:
            raise CanonicalBuildJobExecutionError(
                "El perfil configurado no coincide con profile_path fijado por el crawl"
            )

        # Parse, validate and fingerprint the same bytes once. The canonical builder
        # receives this exact loaded profile so provenance cannot race a second read.
        loaded = ProfileLoader(self.profile_path).load_with_provenance()
        if loaded.sha256 != expected_sha256:
            raise CanonicalBuildJobExecutionError(
                "El perfil configurado cambió desde el crawl fuente (profile_sha256)"
            )
        return actual_path, loaded

    @staticmethod
    def _snapshot_context(
        scope: PipelineJobScope,
        target: str | None,
        parameters: dict[str, Any],
    ) -> CanonicalSnapshotContext:
        if scope == PipelineJobScope.FULL:
            return CanonicalSnapshotContext.full()

        if scope == PipelineJobScope.SCREEN:
            try:
                return CanonicalSnapshotContext(
                    mode="partial",
                    scope="screen",
                    target=target,
                    target_screen_id=str(
                        parameters.get("target_screen_id") or ""
                    ).strip(),
                    erp_id=str(parameters.get("erp_id") or "").strip(),
                    base_knowledge_version_id=str(
                        parameters.get("base_knowledge_version_id") or ""
                    ).strip(),
                    base_knowledge_version=str(
                        parameters.get("base_knowledge_version") or ""
                    ).strip(),
                )
            except ValueError as exc:
                raise CanonicalBuildJobExecutionError(
                    f"El crawl SCREEN no conserva provenance canónica válida: {exc}"
                ) from exc

        if scope == PipelineJobScope.MODULE:
            try:
                return CanonicalSnapshotContext(
                    mode="partial",
                    scope="module",
                    target=target,
                    target_module_id=str(
                        parameters.get("target_module_id") or ""
                    ).strip(),
                    base_knowledge_version_id=str(
                        parameters.get("base_knowledge_version_id") or ""
                    ).strip(),
                    base_knowledge_version=str(
                        parameters.get("base_knowledge_version") or ""
                    ).strip(),
                    erp_id=str(parameters.get("erp_id") or "").strip(),
                )
            except ValueError as exc:
                raise CanonicalBuildJobExecutionError(
                    f"El crawl MODULE no conserva provenance canónica válida: {exc}"
                ) from exc

        raise CanonicalBuildJobExecutionError(
            f"Scope canónico no soportado: {scope.value}"
        )
