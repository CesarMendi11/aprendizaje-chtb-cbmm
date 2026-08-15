from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from src.database.enums import KnowledgeVersionStatus, PipelineJobScope
from src.database.models import KnowledgeVersionRecord
from src.database.services import (
    CanonicalKnowledgeMaterializationError,
    CanonicalKnowledgeMaterializer,
)
from src.knowledge.canonical import (
    CanonicalKnowledgeExporter,
    CanonicalKnowledgeRepository,
    CanonicalPartialMergeError,
    CanonicalPartialMerger,
    CanonicalSnapshotContext,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ProgressCallback = Callable[[str, dict[str, Any]], None]


class CanonicalMergeJobExecutionError(RuntimeError):
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


class CanonicalMergeJobExecutor:
    """Merge one governed MODULE or SCREEN partial into its exact ACTIVE base."""

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
        normalized_scope = PipelineJobScope(scope)
        if normalized_scope not in {PipelineJobScope.MODULE, PipelineJobScope.SCREEN}:
            raise CanonicalMergeJobExecutionError(
                "canonical_merge sólo admite scope MODULE o SCREEN"
            )
        params = dict(parameters or {})
        emit = progress or (lambda _stage, _payload: None)
        source_canonical_job_id = self._uuid_param(params, "source_canonical_job_id")
        source_crawl_job_id = self._uuid_param(params, "source_crawl_job_id")
        base_version_id = self._uuid_param(params, "base_knowledge_version_id")
        base_version_name = self._required_text(params, "base_knowledge_version")
        erp_id = self._required_text(params, "erp_id")
        clean_target = str(target or "").strip()
        target_key = (
            "target_module_id"
            if normalized_scope == PipelineJobScope.MODULE
            else "target_screen_id"
        )
        target_entity_id = self._required_text(params, target_key)
        if normalized_scope == PipelineJobScope.MODULE and target_entity_id != clean_target:
            raise CanonicalMergeJobExecutionError(
                "canonical_merge conserva un target_module_id inconsistente"
            )
        if normalized_scope == PipelineJobScope.SCREEN and not clean_target.startswith("/"):
            raise CanonicalMergeJobExecutionError(
                "canonical_merge SCREEN requiere una ruta objetivo interna"
            )

        run_root = (self.runs_root / str(source_crawl_job_id)).resolve()
        knowledge_path = self._artifact(params.get("knowledge_path"), run_root, "knowledge.json")
        manifest_path = self._artifact(params.get("manifest_path"), run_root, "manifest.json")
        build_report_path = self._artifact(
            params.get("build_report_path"), run_root, "build_report.json"
        )
        if not (knowledge_path.parent == manifest_path.parent == build_report_path.parent):
            raise CanonicalMergeJobExecutionError(
                "Los artefactos canonical partial no pertenecen al mismo directorio"
            )

        emit(
            "loading_partial_canonical",
            {
                "work_units": 1,
                "progress_total": 4,
                "source_canonical_job_id": str(source_canonical_job_id),
                "source_crawl_job_id": str(source_crawl_job_id),
            },
        )
        try:
            repository = CanonicalKnowledgeRepository(knowledge_path)
            partial = repository.knowledge
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("knowledge_version") != partial.knowledge_version:
                raise ValueError("manifest.json no corresponde al canonical partial")
            if manifest.get("canonical_document_hash") != repository.document_hash:
                raise ValueError("Hash del canonical partial no coincide")
            snapshot = CanonicalSnapshotContext.model_validate(manifest.get("snapshot"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalMergeJobExecutionError(
                "No fue posible cargar el canonical partial fuente"
            ) from exc

        expected_partial = str(params.get("expected_partial_knowledge_version") or "").strip()
        if expected_partial and partial.knowledge_version != expected_partial:
            raise CanonicalMergeJobExecutionError(
                "La knowledge_version partial no coincide con el job fuente"
            )
        if (
            snapshot.mode != "partial"
            or snapshot.scope != normalized_scope.value
            or snapshot.target != clean_target
            or snapshot.base_knowledge_version_id != str(base_version_id)
            or snapshot.base_knowledge_version != base_version_name
            or snapshot.erp_id != erp_id
        ):
            raise CanonicalMergeJobExecutionError(
                "La provenance del canonical partial no coincide con el job de merge"
            )
        if normalized_scope == PipelineJobScope.MODULE:
            if snapshot.target_module_id != target_entity_id:
                raise CanonicalMergeJobExecutionError(
                    "La provenance MODULE no coincide con el target fijado"
                )
        elif snapshot.target_screen_id != target_entity_id:
            raise CanonicalMergeJobExecutionError(
                "La provenance SCREEN no coincide con el target fijado"
            )

        emit(
            "materializing_active_base",
            {
                "work_units": 2,
                "progress_total": 4,
                "base_knowledge_version_id": str(base_version_id),
                "base_knowledge_version": base_version_name,
            },
        )
        try:
            with self.session_factory.begin() as session:
                version = session.scalar(
                    select(KnowledgeVersionRecord)
                    .where(KnowledgeVersionRecord.id == base_version_id)
                    .with_for_update()
                )
                if version is None:
                    raise CanonicalMergeJobExecutionError(
                        "La KnowledgeVersion base fijada no existe"
                    )
                if version.status != KnowledgeVersionStatus.ACTIVE:
                    raise CanonicalMergeJobExecutionError(
                        "La KnowledgeVersion base fijada ya no está ACTIVE"
                    )
                if version.knowledge_version != base_version_name:
                    raise CanonicalMergeJobExecutionError(
                        "La knowledge_version base fijada cambió"
                    )
                if version.erp_id != erp_id:
                    raise CanonicalMergeJobExecutionError(
                        "La KnowledgeVersion base pertenece a otro ERP"
                    )
                base = CanonicalKnowledgeMaterializer(session).materialize(
                    base_version_id,
                    require_active=True,
                )
                merged, report = CanonicalPartialMerger().merge(base, partial, snapshot)
        except CanonicalMergeJobExecutionError:
            raise
        except (CanonicalKnowledgeMaterializationError, CanonicalPartialMergeError) as exc:
            raise CanonicalMergeJobExecutionError(str(exc)) from exc
        except Exception as exc:
            raise CanonicalMergeJobExecutionError(str(exc)) from exc

        emit(
            "exporting_full_candidate",
            {
                "work_units": 3,
                "progress_total": 4,
                "knowledge_version": merged.knowledge_version,
            },
        )
        output_dir = run_root / "processed" / "canonical-merged" / str(job_id)
        merge_payload = {
            "source_canonical_job_id": str(source_canonical_job_id),
            "source_crawl_job_id": str(source_crawl_job_id),
            "base_knowledge_version_id": str(base_version_id),
            "base_knowledge_version": base_version_name,
            "erp_id": erp_id,
            **report.as_dict(),
        }
        CanonicalKnowledgeExporter().export(
            merged,
            output_dir,
            pretty=True,
            snapshot_context=CanonicalSnapshotContext.full(),
            manifest_metadata={"merge": merge_payload},
            build_report={
                "snapshot": CanonicalSnapshotContext.full().model_dump(mode="json"),
                "merge": merge_payload,
                "warnings": [item.model_dump(mode="json") for item in merged.build_warnings],
            },
        )

        emit(
            "full_candidate_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": merged.knowledge_version,
                target_key: target_entity_id,
            },
        )
        result = {
            "source_canonical_job_id": str(source_canonical_job_id),
            "source_crawl_job_id": str(source_crawl_job_id),
            "scope": "full",
            "target": None,
            "merged_from_scope": normalized_scope.value,
            "erp_id": erp_id,
            "base_knowledge_version_id": str(base_version_id),
            "base_knowledge_version": base_version_name,
            "partial_knowledge_version": partial.knowledge_version,
            "knowledge_version": merged.knowledge_version,
            "snapshot_mode": "full",
            "snapshot_scope": "full",
            "canonical_dir": _relative_project_path(self.project_root, output_dir),
            "knowledge_path": _relative_project_path(
                self.project_root, output_dir / "knowledge.json"
            ),
            "manifest_path": _relative_project_path(
                self.project_root, output_dir / "manifest.json"
            ),
            "build_report_path": _relative_project_path(
                self.project_root, output_dir / "build_report.json"
            ),
            "merge_report": report.as_dict(),
            "statistics": dict(merged.statistics),
            "target_module_id": snapshot.target_module_id,
            "target_screen_id": snapshot.target_screen_id,
            "partial_target": snapshot.target,
        }
        return result

    @staticmethod
    def _uuid_param(params: dict[str, Any], name: str) -> uuid.UUID:
        raw = params.get(name)
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError) as exc:
            raise CanonicalMergeJobExecutionError(
                f"canonical_merge requiere {name} válido"
            ) from exc

    @staticmethod
    def _required_text(params: dict[str, Any], name: str) -> str:
        value = str(params.get(name) or "").strip()
        if not value:
            raise CanonicalMergeJobExecutionError(f"canonical_merge requiere {name}")
        return value

    def _artifact(self, raw: Any, run_root: Path, expected_name: str) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise CanonicalMergeJobExecutionError(
                f"canonical_merge requiere {expected_name}"
            )
        path = _project_path(self.project_root, raw.strip()).resolve()
        try:
            path.relative_to(run_root)
        except ValueError as exc:
            raise CanonicalMergeJobExecutionError(
                "El artefacto partial está fuera del crawl aislado"
            ) from exc
        if path.name != expected_name or not path.is_file():
            raise CanonicalMergeJobExecutionError(
                f"Artefacto partial no disponible: {expected_name}"
            )
        return path
