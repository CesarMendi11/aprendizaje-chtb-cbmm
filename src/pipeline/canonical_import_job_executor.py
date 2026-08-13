from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select

from src.database.enums import KnowledgeVersionStatus
from src.database.models import KnowledgeVersionRecord, SyncJob
from src.database.services import CanonicalImportService
from src.knowledge.canonical.repository import CanonicalKnowledgeRepository

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ProgressCallback = Callable[[str, dict[str, Any]], None]


class CanonicalImportJobExecutionError(RuntimeError):
    pass


def _project_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relative_project_path(root: Path, value: str | Path) -> str:
    path = Path(value)
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


class CanonicalImportJobExecutor:
    """Import one pipeline canonical snapshot as a non-active staging version.

    The executor deliberately disables activation and projection SyncJobs. An isolated
    screen crawl can therefore be demonstrated end-to-end without replacing the active
    full-ERP knowledge version.
    """

    def __init__(
        self,
        session_factory,
        *,
        project_root: str | Path | None = None,
        runs_root: str | Path | None = None,
    ):
        self.session_factory = session_factory
        self.project_root = Path(project_root or PROJECT_ROOT).resolve()
        configured_runs = runs_root or os.getenv(
            "ERP_ASSISTANT_PIPELINE_RUNS_DIR", "data/runs/pipeline"
        )
        self.runs_root = _project_path(self.project_root, configured_runs).resolve()

    def execute(
        self,
        *,
        job_id: uuid.UUID | str,
        scope,
        target: str | None,
        parameters: dict[str, Any] | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        params = dict(parameters or {})
        emit = progress or (lambda _stage, _payload: None)

        source_canonical_job_id = self._uuid_param(params, "source_canonical_job_id")
        source_crawl_job_id = self._uuid_param(params, "source_crawl_job_id")
        requires_active_base = bool(params.get("requires_active_base"))
        base_version_id = (
            self._uuid_param(params, "base_knowledge_version_id")
            if requires_active_base
            else None
        )
        base_version_name = str(params.get("base_knowledge_version") or "").strip()
        pinned_erp_id = str(params.get("erp_id") or "").strip()
        if requires_active_base and (not base_version_name or not pinned_erp_id):
            raise CanonicalImportJobExecutionError(
                "canonical_import merged requiere versión base y ERP fijados"
            )
        run_root = (self.runs_root / str(source_crawl_job_id)).resolve()

        emit(
            "loading_canonical",
            {
                "work_units": 1,
                "progress_total": 4,
                "source_canonical_job_id": str(source_canonical_job_id),
                "source_crawl_job_id": str(source_crawl_job_id),
            },
        )

        knowledge_path = self._artifact(
            params.get("knowledge_path"), run_root, "knowledge.json"
        )
        manifest_path = self._artifact(
            params.get("manifest_path"), run_root, "manifest.json"
        )
        build_report_path = self._artifact(
            params.get("build_report_path"), run_root, "build_report.json"
        )
        if not (
            knowledge_path.parent == manifest_path.parent == build_report_path.parent
        ):
            raise CanonicalImportJobExecutionError(
                "Los artefactos canónicos fuente no pertenecen al mismo directorio"
            )

        try:
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            knowledge = CanonicalKnowledgeRepository(knowledge_path).knowledge
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalImportJobExecutionError(
                "No fue posible cargar el conocimiento canónico fuente"
            ) from exc

        snapshot = manifest_payload.get("snapshot")
        if isinstance(snapshot, dict) and snapshot.get("mode") == "partial":
            raise CanonicalImportJobExecutionError(
                "Un canonical parcial debe fusionarse con su versión base antes de importarse"
            )

        expected_version = str(params.get("expected_knowledge_version") or "").strip()
        if expected_version and knowledge.knowledge_version != expected_version:
            raise CanonicalImportJobExecutionError(
                "La versión canónica no coincide con el job fuente"
            )
        if requires_active_base and knowledge.erp_system.id != pinned_erp_id:
            raise CanonicalImportJobExecutionError(
                "El canonical merged pertenece a un ERP distinto de su base fijada"
            )
        if requires_active_base:
            merge_context = manifest_payload.get("merge")
            expected_target = str(params.get("merged_target_module_id") or "").strip()
            if not isinstance(merge_context, dict) or (
                str(merge_context.get("base_knowledge_version_id") or "")
                != str(base_version_id)
                or str(merge_context.get("base_knowledge_version") or "")
                != base_version_name
                or str(merge_context.get("erp_id") or "") != pinned_erp_id
                or (
                    expected_target
                    and str(merge_context.get("target_module_id") or "") != expected_target
                )
            ):
                raise CanonicalImportJobExecutionError(
                    "El manifest merged no conserva la provenance base esperada"
                )

        emit(
            "validating_import",
            {
                "work_units": 2,
                "progress_total": 4,
                "knowledge_version": knowledge.knowledge_version,
                "erp_id": knowledge.erp_system.id,
            },
        )

        emit(
            "importing_staging",
            {
                "work_units": 3,
                "progress_total": 4,
                "knowledge_version": knowledge.knowledge_version,
            },
        )
        try:
            with self.session_factory.begin() as session:
                if requires_active_base:
                    base = session.scalar(
                        select(KnowledgeVersionRecord)
                        .where(KnowledgeVersionRecord.id == base_version_id)
                        .with_for_update()
                    )
                    if (
                        base is None
                        or base.status != KnowledgeVersionStatus.ACTIVE
                        or base.knowledge_version != base_version_name
                        or base.erp_id != pinned_erp_id
                    ):
                        raise CanonicalImportJobExecutionError(
                            "La versión base fijada del canonical merged ya no está ACTIVE"
                        )
                result = CanonicalImportService(session).import_canonical(
                    knowledge_path,
                    manifest_path,
                    build_report_path,
                    activate=False,
                    create_sync_jobs=False,
                )
                if not result.version_id:
                    raise CanonicalImportJobExecutionError(
                        "La importación no devolvió KnowledgeVersion"
                    )
                version_id = uuid.UUID(result.version_id)
                version = session.get(KnowledgeVersionRecord, version_id)
                if version is None:
                    raise CanonicalImportJobExecutionError(
                        "KnowledgeVersion importada no encontrada"
                    )
                sync_jobs = session.scalar(
                    select(func.count())
                    .select_from(SyncJob)
                    .where(SyncJob.knowledge_version_id == version.id)
                ) or 0
                version_status = version.status
                erp_id = version.erp_id
        except CanonicalImportJobExecutionError:
            raise
        except Exception as exc:
            raise CanonicalImportJobExecutionError(str(exc)) from exc

        emit(
            "staging_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": result.knowledge_version,
                "knowledge_version_id": result.version_id,
                "version_status": str(getattr(version_status, "value", version_status)),
            },
        )

        return {
            "source_canonical_job_id": str(source_canonical_job_id),
            "source_crawl_job_id": str(source_crawl_job_id),
            "scope": str(getattr(scope, "value", scope)),
            "target": target,
            "erp_id": erp_id,
            "knowledge_version": result.knowledge_version,
            "knowledge_version_id": result.version_id,
            "version_status": str(getattr(version_status, "value", version_status)),
            "import_result": result.result,
            "items": result.items,
            "carried_reviews": result.carried_reviews,
            "warnings": result.warnings,
            "activation_performed": False,
            "sync_jobs_created": False,
            "sync_jobs_present": int(sync_jobs),
            "staging_ready": version_status == KnowledgeVersionStatus.IMPORTED,
            "base_knowledge_version_id": (
                str(base_version_id) if base_version_id is not None else None
            ),
            "base_knowledge_version": base_version_name or None,
            "knowledge_path": _relative_project_path(self.project_root, knowledge_path),
            "manifest_path": _relative_project_path(self.project_root, manifest_path),
            "build_report_path": _relative_project_path(
                self.project_root, build_report_path
            ),
        }

    @staticmethod
    def _uuid_param(params: dict[str, Any], name: str) -> uuid.UUID:
        raw = params.get(name)
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError) as exc:
            raise CanonicalImportJobExecutionError(
                f"canonical_import requiere {name} válido"
            ) from exc

    def _artifact(self, raw: Any, run_root: Path, expected_name: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise CanonicalImportJobExecutionError(
                f"canonical_import requiere {expected_name}"
            )
        path = _project_path(self.project_root, raw.strip()).resolve()
        try:
            path.relative_to(run_root)
        except ValueError as exc:
            raise CanonicalImportJobExecutionError(
                "El artefacto canónico está fuera del crawl aislado"
            ) from exc
        if path.name != expected_name or not path.is_file():
            raise CanonicalImportJobExecutionError(
                f"Artefacto canónico no disponible: {expected_name}"
            )
        return path
