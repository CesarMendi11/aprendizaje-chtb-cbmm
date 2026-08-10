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
        builder = CanonicalKnowledgeBuilder(PROJECT_ROOT)
        try:
            knowledge = builder.build_from_paths(
                self.profile_path,
                structural_dir=structural_dir,
            )
        except (ArtifactLoadError, OSError, ValueError) as exc:
            raise CanonicalBuildJobExecutionError(str(exc)) from exc

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
        report = builder.build_report(knowledge, issues)

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
        }
