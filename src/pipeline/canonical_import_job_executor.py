from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select

from src.config.pipeline_settings import PipelineSettings
from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)
from src.database.models import KnowledgeItem, KnowledgeVersionRecord, PipelineJob, SyncJob
from src.database.services import CanonicalImportService
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.repository import CanonicalKnowledgeRepository
from src.knowledge.canonical.snapshot import CanonicalSnapshotContext
from src.knowledge.crawl_execution_quality import (
    CrawlExecutionQualityError,
    validate_certified_quality_source,
    validate_matching_certified_quality,
)

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
        configured_runs = runs_root or PipelineSettings().runs_root
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
        if params.get("source_reconciliation_job_id") is not None:
            return self._execute_reconciliation_source(
                job_id=job_id,
                scope=scope,
                target=target,
                params=params,
                progress=progress,
            )
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
            build_report_payload = json.loads(
                build_report_path.read_text(encoding="utf-8")
            )
            knowledge = CanonicalKnowledgeRepository(knowledge_path).knowledge
            execution_quality = validate_matching_certified_quality(
                build_report_payload.get("crawl_execution_quality"),
                manifest_payload.get("crawl_execution_quality"),
                params.get("expected_crawl_execution_quality"),
            )
            execution_quality = validate_certified_quality_source(
                execution_quality,
                source_run_id=source_crawl_job_id,
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            CrawlExecutionQualityError,
        ) as exc:
            raise CanonicalImportJobExecutionError(
                "No fue posible cargar un canonical con calidad de crawl certificada"
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
            expected_scope = str(params.get("merged_from_scope") or "").strip()
            if expected_scope not in {"module", "screen"}:
                raise CanonicalImportJobExecutionError(
                    "canonical_import merged requiere merged_from_scope válido"
                )
            target_key = (
                "target_module_id" if expected_scope == "module" else "target_screen_id"
            )
            expected_target = str(params.get(f"merged_{target_key}") or "").strip()
            if not expected_target:
                raise CanonicalImportJobExecutionError(
                    "canonical_import merged requiere target parcial fijado"
                )
            if not isinstance(merge_context, dict) or (
                str(merge_context.get("base_knowledge_version_id") or "")
                != str(base_version_id)
                or str(merge_context.get("base_knowledge_version") or "")
                != base_version_name
                or str(merge_context.get("erp_id") or "") != pinned_erp_id
                or str(merge_context.get("scope") or "") != expected_scope
                or str(merge_context.get(target_key) or "") != expected_target
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
            "crawl_execution_quality": execution_quality,
            "knowledge_path": _relative_project_path(self.project_root, knowledge_path),
            "manifest_path": _relative_project_path(self.project_root, manifest_path),
            "build_report_path": _relative_project_path(
                self.project_root, build_report_path
            ),
        }

    def _execute_reconciliation_source(
        self,
        *,
        job_id,
        scope,
        target,
        params: dict[str, Any],
        progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        if PipelineJobScope(scope) != PipelineJobScope.VERSION or target is not None:
            raise CanonicalImportJobExecutionError(
                "canonical_import reconciliation requiere scope VERSION sin target"
            )
        source_id = self._uuid_param(params, "source_reconciliation_job_id")
        activation_mode = self._required_text(params, "activation_mode")
        if activation_mode != "staging_only":
            raise CanonicalImportJobExecutionError(
                "canonical_import reconciliation requiere activation_mode=staging_only"
            )
        expected = {
            "erp_id": self._required_text(params, "erp_id"),
            "knowledge_version": self._required_text(params, "expected_knowledge_version"),
            "decision_set_hash": self._required_text(params, "expected_decision_set_hash"),
            "raw_candidate_version_id": str(
                self._uuid_param(params, "raw_candidate_version_id")
            ),
            "base_active_version_id": str(self._uuid_param(params, "base_active_version_id")),
        }
        emit = progress or (lambda _stage, _payload: None)
        emit(
            "loading_reconciliation_source",
            {
                "work_units": 1,
                "progress_total": 4,
                "source_reconciliation_job_id": str(source_id),
            },
        )
        try:
            with self.session_factory.begin() as session:
                source = session.scalar(
                    select(PipelineJob)
                    .where(PipelineJob.id == source_id)
                    .with_for_update()
                )
                result_payload = self._source_result(source)
                self._validate_source_job(source, result_payload, source_id)
                self._validate_import_pins(expected, result_payload)
                raw, active = self._pinned_versions(session, result_payload)
                artifact_dir, knowledge_path, manifest_path, build_report_path = (
                    self._reconciliation_artifacts(result_payload, source_id)
                )
                repository, manifest, build_report = self._validate_reconciliation_artifact(
                    knowledge_path,
                    manifest_path,
                    build_report_path,
                    result_payload,
                )
                existing = session.scalar(
                    select(KnowledgeVersionRecord).where(
                        KnowledgeVersionRecord.erp_id == result_payload["erp_id"],
                        KnowledgeVersionRecord.knowledge_version
                        == result_payload["reconciled_knowledge_version"],
                    )
                )
                if existing is not None and existing.canonical_hash != repository.document_hash:
                    raise CanonicalImportJobExecutionError(
                        "La KnowledgeVersion reconciliada existente no corresponde al artifact."
                    )
                emit(
                    "importing_reconciled_staging",
                    {
                        "work_units": 3,
                        "progress_total": 4,
                        "knowledge_version": repository.knowledge.knowledge_version,
                    },
                )
                imported = CanonicalImportService(session).import_canonical(
                    knowledge_path,
                    manifest_path,
                    build_report_path,
                    activate=False,
                    create_sync_jobs=False,
                )
                if not imported.version_id:
                    raise CanonicalImportJobExecutionError(
                        "La importación reconciliada no devolvió KnowledgeVersion."
                    )
                version = session.get(KnowledgeVersionRecord, uuid.UUID(imported.version_id))
                if (
                    version is None
                    or version.erp_id != result_payload["erp_id"]
                    or version.knowledge_version != result_payload["reconciled_knowledge_version"]
                    or version.status != KnowledgeVersionStatus.IMPORTED
                    or version.canonical_hash != repository.document_hash
                ):
                    raise CanonicalImportJobExecutionError(
                        "La KnowledgeVersion reconciliada importada es inconsistente."
                    )
                item_count = session.scalar(
                    select(func.count())
                    .select_from(KnowledgeItem)
                    .where(KnowledgeItem.knowledge_version_id == version.id)
                ) or 0
                sync_jobs = session.scalar(
                    select(func.count())
                    .select_from(SyncJob)
                    .where(SyncJob.knowledge_version_id == version.id)
                ) or 0
                if item_count != result_payload["reconciled_item_total"] or sync_jobs != 0:
                    raise CanonicalImportJobExecutionError(
                        "Los items o SyncJobs reconciliados no coinciden con provenance."
                    )
                if (
                    raw.status != KnowledgeVersionStatus.IMPORTED
                    or active.status != KnowledgeVersionStatus.ACTIVE
                ):
                    raise CanonicalImportJobExecutionError(
                        "RAW candidate o ACTIVE cambió durante la importación reconciliada."
                    )
                final_payload = {
                    "source_reconciliation_job_id": str(source_id),
                    "scope": "version",
                    "target": None,
                    "erp_id": version.erp_id,
                    "knowledge_version_id": str(version.id),
                    "knowledge_version": version.knowledge_version,
                    "version_status": "imported",
                    "import_result": imported.result,
                    "items": imported.items,
                    "carried_reviews": imported.carried_reviews,
                    "warnings": imported.warnings,
                    "sync_jobs": 0,
                    "sync_jobs_created": False,
                    "sync_jobs_present": 0,
                    "staging_ready": True,
                    "activation_performed": False,
                    "raw_candidate_version_id": result_payload["raw_candidate_version_id"],
                    "raw_candidate_knowledge_version": result_payload[
                        "raw_candidate_knowledge_version"
                    ],
                    "base_active_version_id": result_payload["base_active_version_id"],
                    "base_active_knowledge_version": result_payload[
                        "base_active_knowledge_version"
                    ],
                    "decision_set_hash": result_payload["decision_set_hash"],
                    "knowledge_path": _relative_project_path(self.project_root, knowledge_path),
                    "manifest_path": _relative_project_path(self.project_root, manifest_path),
                    "build_report_path": _relative_project_path(
                        self.project_root, build_report_path
                    ),
                    "source_generator_version": result_payload["generator_version"],
                }
        except CanonicalImportJobExecutionError:
            raise
        except Exception as exc:
            raise CanonicalImportJobExecutionError(str(exc)) from exc

        return final_payload

    def _source_result(self, source) -> dict[str, Any]:
        if source is None or not isinstance(source.result_payload, dict):
            raise CanonicalImportJobExecutionError(
                "El source reconciliation job no tiene result_payload válido."
            )
        return dict(source.result_payload)

    def _validate_source_job(self, source, payload, source_id) -> None:
        required = (
            "erp_id", "raw_candidate_version_id", "raw_candidate_knowledge_version",
            "base_active_version_id", "base_active_knowledge_version", "knowledge_version",
            "reconciled_knowledge_version", "canonical_dir", "knowledge_path",
            "manifest_path", "build_report_path", "decision_set_hash",
            "raw_candidate_item_total", "active_item_total", "reconciled_item_total",
            "retain_from_active_total", "confirmed_removed_total", "unresolved_total",
            "decisions", "generator_version", "candidate_origin",
        )
        if (
            source.kind != PipelineJobKind.CANONICAL_RECONCILIATION
            or source.status != PipelineJobStatus.SUCCEEDED
            or source.scope != PipelineJobScope.VERSION
            or source.target is not None
            or source.erp_id != payload["erp_id"]
            or not isinstance(source.parameters, dict)
            or any(name not in payload or payload[name] is None for name in required)
            or payload["knowledge_version"] != payload["reconciled_knowledge_version"]
            or payload["unresolved_total"] != 0
            or not isinstance(payload["decisions"], list)
        ):
            raise CanonicalImportJobExecutionError(
                "El source reconciliation job no satisface el contrato gobernado."
            )
        raw_id = self._uuid_param(payload, "raw_candidate_version_id")
        if source.id != source_id or source.knowledge_version_id != raw_id:
            raise CanonicalImportJobExecutionError(
                "El source reconciliation job no conserva el RAW candidate fijado."
            )
        expected_parameters = {
            "candidate_version_id": payload["raw_candidate_version_id"],
            "candidate_knowledge_version": payload["raw_candidate_knowledge_version"],
            "active_version_id": payload["base_active_version_id"],
            "active_knowledge_version": payload["base_active_knowledge_version"],
            "erp_id": payload["erp_id"],
        }
        if any(source.parameters.get(key) != value for key, value in expected_parameters.items()):
            raise CanonicalImportJobExecutionError(
                "Los parameters del source reconciliation job son inconsistentes."
            )
        if content_hash(payload["decisions"]) != payload["decision_set_hash"]:
            raise CanonicalImportJobExecutionError(
                "El decision_set_hash del source reconciliation job no coincide."
            )
        decisions = payload["decisions"]
        allowed = {"retain_from_active", "confirmed_remove"}
        if any(
            not isinstance(value, dict)
            or value.get("decision") not in allowed
            or value.get("requires_human_review") is not False
            or not value.get("review_set_id")
            or not value.get("review_decision_id")
            or not value.get("review_action_id")
            or not isinstance(value.get("review_revision"), int)
            or value["review_revision"] <= 0
            for value in decisions
        ):
            raise CanonicalImportJobExecutionError(
                "El source reconciliation no conserva Removal HITL resuelto."
            )
        review_set_ids = {value["review_set_id"] for value in decisions}
        if len(review_set_ids) > 1:
            raise CanonicalImportJobExecutionError(
                "El source reconciliation mezcla removal review sets."
            )
        retained = sum(value["decision"] == "retain_from_active" for value in decisions)
        removed = sum(value["decision"] == "confirmed_remove" for value in decisions)
        if (
            retained != payload["retain_from_active_total"]
            or removed != payload["confirmed_removed_total"]
            or len(decisions) != retained + removed
        ):
            raise CanonicalImportJobExecutionError(
                "Los totales Removal HITL del source reconciliation son inconsistentes."
            )

    @staticmethod
    def _validate_import_pins(expected, payload) -> None:
        if (
            expected["erp_id"] != payload["erp_id"]
            or expected["knowledge_version"] != payload["reconciled_knowledge_version"]
            or expected["decision_set_hash"] != payload["decision_set_hash"]
            or expected["raw_candidate_version_id"] != payload["raw_candidate_version_id"]
            or expected["base_active_version_id"] != payload["base_active_version_id"]
        ):
            raise CanonicalImportJobExecutionError(
                "Los pins del canonical_import no coinciden con reconciliation."
            )

    def _pinned_versions(self, session, payload):
        raw_id = self._uuid_param(payload, "raw_candidate_version_id")
        active_id = self._uuid_param(payload, "base_active_version_id")
        raw = session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.id == raw_id)
            .with_for_update()
        )
        active = session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.id == active_id)
            .with_for_update()
        )
        if (
            raw is None
            or raw.status != KnowledgeVersionStatus.IMPORTED
            or raw.knowledge_version != payload["raw_candidate_knowledge_version"]
            or raw.erp_id != payload["erp_id"]
            or active is None
            or active.status != KnowledgeVersionStatus.ACTIVE
            or active.knowledge_version != payload["base_active_knowledge_version"]
            or active.erp_id != payload["erp_id"]
        ):
            raise CanonicalImportJobExecutionError(
                "Los pins RAW candidate/ACTIVE de reconciliation ya no son válidos."
            )
        return raw, active

    def _reconciliation_artifacts(self, payload, source_id):
        source_root = (self.runs_root / "reconciliation" / str(source_id)).resolve()
        canonical_dir = _project_path(self.project_root, payload["canonical_dir"]).resolve()
        if canonical_dir != source_root:
            raise CanonicalImportJobExecutionError(
                "canonical_dir no pertenece al reconciliation job autorizado."
            )
        paths = tuple(
            self._reconciliation_artifact(payload[name], source_root, name)
            for name in ("knowledge_path", "manifest_path", "build_report_path")
        )
        if any(path.parent != canonical_dir for path in paths):
            raise CanonicalImportJobExecutionError(
                "Los artifacts reconciliation no pertenecen al canonical_dir autorizado."
            )
        return canonical_dir, *paths

    def _reconciliation_artifact(self, raw, source_root, field):
        expected_name = field.removesuffix("_path") + ".json"
        path = _project_path(self.project_root, raw).resolve()
        try:
            path.relative_to(source_root)
        except ValueError as exc:
            raise CanonicalImportJobExecutionError(
                "Artifact reconciliation fuera del directorio autorizado."
            ) from exc
        if path.name != expected_name or not path.is_file():
            raise CanonicalImportJobExecutionError(
                f"Artifact reconciliation no disponible: {expected_name}"
            )
        return path

    def _validate_reconciliation_artifact(
        self, knowledge_path, manifest_path, build_report_path, payload
    ):
        try:
            repository = CanonicalKnowledgeRepository(knowledge_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            build_report = json.loads(build_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CanonicalImportJobExecutionError(
                "No fue posible cargar los artifacts reconciliation."
            ) from exc
        metadata = {
            key: payload[key]
            for key in (
                "raw_candidate_version_id", "raw_candidate_knowledge_version",
                "base_active_version_id", "base_active_knowledge_version", "erp_id",
                "candidate_origin", "decision_set_hash", "retain_from_active_total",
                "confirmed_removed_total", "unresolved_total",
            )
        }
        if (
            repository.knowledge.knowledge_version != payload["reconciled_knowledge_version"]
            or repository.knowledge.erp_system.id != payload["erp_id"]
            or manifest.get("knowledge_version") != repository.knowledge.knowledge_version
            or manifest.get("canonical_document_hash") != repository.document_hash
            or manifest.get("snapshot") != CanonicalSnapshotContext.full().model_dump(mode="json")
            or manifest.get("reconciliation") != metadata
            or build_report.get("reconciliation") != metadata
            or build_report.get("decision_set_hash") != payload["decision_set_hash"]
            or build_report.get("counts")
            != {
                "raw_candidate_item_total": payload["raw_candidate_item_total"],
                "active_item_total": payload["active_item_total"],
                "reconciled_item_total": payload["reconciled_item_total"],
            }
        ):
            raise CanonicalImportJobExecutionError(
                "Los artifacts reconciliation no coinciden con provenance."
            )
        return repository, manifest, build_report

    @staticmethod
    def _uuid_param(params: dict[str, Any], name: str) -> uuid.UUID:
        raw = params.get(name)
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError) as exc:
            raise CanonicalImportJobExecutionError(
                f"canonical_import requiere {name} válido"
            ) from exc

    @staticmethod
    def _required_text(params: dict[str, Any], name: str) -> str:
        value = str(params.get(name) or "").strip()
        if not value:
            raise CanonicalImportJobExecutionError(f"canonical_import requiere {name}")
        return value

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
