from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Callable

from src.config.profile_loader import ProfileLoader
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
    Its output stays under the source crawl run so a short demo crawl cannot replace
    the stable 52-screen snapshot.
    """

    def __init__(
        self,
        *,
        profile_path: str | Path | None = None,
        runs_root: str | Path | None = None,
    ):
        configured_profile = profile_path or os.getenv(
            "ERP_ASSISTANT_CRAWL_PROFILE", "configs/cbmm.yaml"
        )
        configured_runs = runs_root or os.getenv(
            "ERP_ASSISTANT_PIPELINE_RUNS_DIR", "data/runs/pipeline"
        )
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
        if not self.profile_path.is_file():
            raise CanonicalBuildJobExecutionError("Perfil de crawler no encontrado")

        # Validate the profile before the canonical builder consumes it so failures
        # are reported as a controlled pipeline error instead of a partial export.
        ProfileLoader(self.profile_path).load()

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
        )

        return {
            "source_crawl_job_id": str(source_job_id),
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
        }

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
