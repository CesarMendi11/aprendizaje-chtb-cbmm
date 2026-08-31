from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from erp_assistant.config.paths import PROJECT_ROOT

from sqlalchemy import select

from erp_assistant.config.pipeline_settings import PipelineSettings
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus, PipelineJobScope
from erp_assistant.persistence.postgres.models import KnowledgeVersionRecord
from erp_assistant.structural.services.canonical_reconciliation_service import (
    CanonicalReconciliationError,
    CanonicalReconciliationService,
)
from erp_assistant.structural.canonical import (
    CanonicalKnowledgeExporter,
    CanonicalKnowledgeRepository,
    CanonicalSnapshotContext,
)
from erp_assistant.structural.canonical.ids import content_hash

ProgressCallback = Callable[[str, dict[str, Any]], None]


class CanonicalReconciliationJobExecutionError(RuntimeError):
    pass


def _project_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relative_project_path(root: Path, value: Path) -> str:
    try:
        return str(value.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(value)


class CanonicalReconciliationJobExecutor:
    """Export one governed, in-memory reconciliation as an isolated FULL artifact."""

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
        if PipelineJobScope(scope) != PipelineJobScope.VERSION or target is not None:
            raise CanonicalReconciliationJobExecutionError(
                "canonical_reconciliation requiere scope VERSION sin target."
            )
        params = dict(parameters or {})
        pins = self._pins(params)
        safe_job_id = self._uuid_param({"job_id": job_id}, "job_id")
        output_dir = (self.runs_root / "reconciliation" / str(safe_job_id)).resolve()
        self._require_within_runs(output_dir)
        emit = progress or (lambda _stage, _payload: None)

        emit(
            "validating_reconciliation_context",
            {
                "work_units": 1,
                "progress_total": 4,
                **self._checkpoint_pins(pins),
            },
        )
        try:
            with self.session_factory.begin() as session:
                candidate, active = self._pinned_versions(session, pins)
                result = CanonicalReconciliationService(session).reconcile(candidate.id)
                plan = result.plan
                self._validate_result(result, plan, candidate, active, pins)
                # Recheck the pinned database records immediately before I/O.
                candidate, active = self._pinned_versions(session, pins)
                self._validate_result(result, plan, candidate, active, pins)

                decisions = self._normalized_decisions(plan)
                decision_set_hash = content_hash(decisions)
                reconciliation = self._reconciliation_metadata(
                    result, decisions, decision_set_hash
                )
                snapshot = CanonicalSnapshotContext.full()
                emit(
                    "exporting_reconciled_canonical",
                    {
                        "work_units": 2,
                        "progress_total": 4,
                        "knowledge_version": result.canonical.knowledge_version,
                    },
                )
                CanonicalKnowledgeExporter().export(
                    result.canonical,
                    output_dir,
                    pretty=True,
                    snapshot_context=snapshot,
                    manifest_metadata={"reconciliation": reconciliation},
                    build_report={
                        "snapshot": snapshot.model_dump(mode="json"),
                        "reconciliation": reconciliation,
                        "decision_set_hash": decision_set_hash,
                        "counts": {
                            "raw_candidate_item_total": result.raw_candidate_item_total,
                            "active_item_total": result.active_item_total,
                            "reconciled_item_total": result.reconciled_item_total,
                        },
                        "warnings": [
                            item.model_dump(mode="json")
                            for item in result.canonical.build_warnings
                        ],
                    },
                )
                repository = CanonicalKnowledgeRepository(output_dir / "knowledge.json")
                exported = repository.knowledge
                if exported.knowledge_version != result.canonical.knowledge_version:
                    raise CanonicalReconciliationJobExecutionError(
                        "El artifact exportado no conserva la knowledge_version reconciliada."
                    )
                self._validate_exported_artifacts(
                    output_dir,
                    repository,
                    result,
                    snapshot,
                    reconciliation,
                    decision_set_hash,
                )
        except CanonicalReconciliationJobExecutionError:
            raise
        except (
            CanonicalReconciliationError,
            OSError,
            ValueError,
        ) as exc:
            raise CanonicalReconciliationJobExecutionError(str(exc)) from exc

        emit(
            "reconciled_canonical_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "knowledge_version": result.canonical.knowledge_version,
                **self._checkpoint_pins(pins),
            },
        )
        return {
            "erp_id": result.erp_id,
            "candidate_origin": result.candidate_origin,
            "raw_candidate_version_id": result.candidate_version_id,
            "raw_candidate_knowledge_version": result.candidate_knowledge_version,
            "base_active_version_id": result.active_version_id,
            "base_active_knowledge_version": result.active_knowledge_version,
            "knowledge_version": result.canonical.knowledge_version,
            "reconciled_knowledge_version": result.canonical.knowledge_version,
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
            "raw_candidate_item_total": result.raw_candidate_item_total,
            "active_item_total": result.active_item_total,
            "reconciled_item_total": result.reconciled_item_total,
            "retain_from_active_total": result.retained_from_active_total,
            "confirmed_removed_total": result.confirmed_removed_total,
            "unresolved_total": result.unresolved_total,
            "decision_set_hash": decision_set_hash,
            "decisions": decisions,
            "generator_version": result.canonical.generator_version,
        }

    def _pinned_versions(self, session, pins):
        candidate = session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.id == pins["candidate_version_id"])
            .with_for_update()
        )
        active = session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.id == pins["active_version_id"])
            .with_for_update()
        )
        if candidate is None:
            raise CanonicalReconciliationJobExecutionError("RAW candidate no existe.")
        if active is None:
            raise CanonicalReconciliationJobExecutionError("ACTIVE fijada no existe.")
        if candidate.status != KnowledgeVersionStatus.IMPORTED:
            raise CanonicalReconciliationJobExecutionError("RAW candidate no está IMPORTED.")
        if active.status != KnowledgeVersionStatus.ACTIVE:
            raise CanonicalReconciliationJobExecutionError("ACTIVE fijada ya no está ACTIVE.")
        if candidate.knowledge_version != pins["candidate_knowledge_version"]:
            raise CanonicalReconciliationJobExecutionError(
                "La knowledge_version RAW no coincide con el pin."
            )
        if active.knowledge_version != pins["active_knowledge_version"]:
            raise CanonicalReconciliationJobExecutionError(
                "La knowledge_version ACTIVE no coincide con el pin."
            )
        if candidate.erp_id != pins["erp_id"] or active.erp_id != pins["erp_id"]:
            raise CanonicalReconciliationJobExecutionError("El ERP fijado no coincide.")
        if candidate.id == active.id:
            raise CanonicalReconciliationJobExecutionError(
                "RAW candidate y ACTIVE deben ser distintos."
            )
        return candidate, active

    @staticmethod
    def _validate_result(result, plan, candidate, active, pins) -> None:
        if (
            result.candidate_version_id != str(candidate.id)
            or result.candidate_knowledge_version != candidate.knowledge_version
            or result.active_version_id != str(active.id)
            or result.active_knowledge_version != active.knowledge_version
            or result.erp_id != pins["erp_id"]
            or result.canonical.erp_system.id != pins["erp_id"]
            or plan.candidate_version_id != result.candidate_version_id
            or plan.active_version_id != result.active_version_id
            or plan.candidate_knowledge_version != result.candidate_knowledge_version
            or plan.active_knowledge_version != result.active_knowledge_version
            or plan.retain_from_active_total != result.retained_from_active_total
            or plan.confirmed_removed_total != result.confirmed_removed_total
            or plan.unresolved_total != result.unresolved_total
        ):
            raise CanonicalReconciliationJobExecutionError(
                "CanonicalReconciliationService devolvió un contexto distinto al fijado."
            )
        if result.unresolved_total != 0:
            raise CanonicalReconciliationJobExecutionError(
                "La reconciliación contiene REMOVED UNRESOLVED."
            )
        if any(
            value.requires_human_review
            or not value.review_set_id
            or not value.review_decision_id
            or not value.review_action_id
            or not isinstance(value.review_revision, int)
            or value.review_revision <= 0
            for value in plan.decisions
        ):
            raise CanonicalReconciliationJobExecutionError(
                "Canonical reconciliation requiere Removal HITL resuelto y trazable."
            )
        if (
            len(plan.decisions)
            != result.retained_from_active_total + result.confirmed_removed_total
        ):
            raise CanonicalReconciliationJobExecutionError(
                "Los totales del Removal HITL no coinciden con sus decisiones."
            )

    @staticmethod
    def _normalized_decisions(plan) -> list[dict[str, Any]]:
        decisions = [
            {
                "entity_type": value.entity_type,
                "canonical_id": value.canonical_id,
                "active_item_id": value.active_item_id,
                "candidate_item_id": value.candidate_item_id,
                "screen_id": value.screen_id,
                "decision": value.decision.value,
                "reason": value.reason,
                "removal_confirmation": value.removal_confirmation,
                "requires_human_review": value.requires_human_review,
                "review_set_id": value.review_set_id,
                "review_decision_id": value.review_decision_id,
                "review_action_id": value.review_action_id,
                "review_revision": value.review_revision,
            }
            for value in plan.decisions
        ]
        decisions.sort(key=lambda value: (value["entity_type"], value["canonical_id"]))
        return decisions

    @staticmethod
    def _reconciliation_metadata(result, decisions, decision_set_hash):
        return {
            "raw_candidate_version_id": result.candidate_version_id,
            "raw_candidate_knowledge_version": result.candidate_knowledge_version,
            "base_active_version_id": result.active_version_id,
            "base_active_knowledge_version": result.active_knowledge_version,
            "erp_id": result.erp_id,
            "candidate_origin": result.candidate_origin,
            "decision_set_hash": decision_set_hash,
            "retain_from_active_total": result.retained_from_active_total,
            "confirmed_removed_total": result.confirmed_removed_total,
            "unresolved_total": result.unresolved_total,
        }

    @staticmethod
    def _uuid_param(params: dict[str, Any], name: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(params.get(name)))
        except (TypeError, ValueError) as exc:
            raise CanonicalReconciliationJobExecutionError(
                f"canonical_reconciliation requiere {name} válido."
            ) from exc

    def _pins(self, params: dict[str, Any]) -> dict[str, Any]:
        pins = {
            "candidate_version_id": self._uuid_param(params, "candidate_version_id"),
            "active_version_id": self._uuid_param(params, "active_version_id"),
            "candidate_knowledge_version": self._required_text(
                params, "candidate_knowledge_version"
            ),
            "active_knowledge_version": self._required_text(
                params, "active_knowledge_version"
            ),
            "erp_id": self._required_text(params, "erp_id"),
        }
        return pins

    @staticmethod
    def _checkpoint_pins(pins: dict[str, Any]) -> dict[str, str]:
        return {
            "candidate_version_id": str(pins["candidate_version_id"]),
            "candidate_knowledge_version": pins["candidate_knowledge_version"],
            "active_version_id": str(pins["active_version_id"]),
            "active_knowledge_version": pins["active_knowledge_version"],
            "erp_id": pins["erp_id"],
        }

    @staticmethod
    def _validate_exported_artifacts(
        output_dir,
        repository,
        result,
        snapshot,
        reconciliation,
        decision_set_hash,
    ) -> None:
        try:
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            build_report = json.loads(
                (output_dir / "build_report.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise CanonicalReconciliationJobExecutionError(
                "No fue posible validar los artifacts reconciliation exportados."
            ) from exc
        expected_metadata = {
            "raw_candidate_version_id": result.candidate_version_id,
            "raw_candidate_knowledge_version": result.candidate_knowledge_version,
            "base_active_version_id": result.active_version_id,
            "base_active_knowledge_version": result.active_knowledge_version,
            "erp_id": result.erp_id,
            "candidate_origin": result.candidate_origin,
            "decision_set_hash": decision_set_hash,
            "retain_from_active_total": result.retained_from_active_total,
            "confirmed_removed_total": result.confirmed_removed_total,
            "unresolved_total": result.unresolved_total,
        }
        if (
            manifest.get("knowledge_version") != result.canonical.knowledge_version
            or manifest.get("canonical_document_hash") != repository.document_hash
            or manifest.get("snapshot") != snapshot.model_dump(mode="json")
            or build_report.get("decision_set_hash") != decision_set_hash
            or manifest.get("reconciliation") != expected_metadata
            or build_report.get("reconciliation") != expected_metadata
            or reconciliation != expected_metadata
        ):
            raise CanonicalReconciliationJobExecutionError(
                "Los artifacts reconciliation exportados son inconsistentes."
            )

    @staticmethod
    def _required_text(params: dict[str, Any], name: str) -> str:
        value = str(params.get(name) or "").strip()
        if not value:
            raise CanonicalReconciliationJobExecutionError(
                f"canonical_reconciliation requiere {name}."
            )
        return value

    def _require_within_runs(self, path: Path) -> None:
        try:
            path.relative_to(self.runs_root)
        except ValueError as exc:
            raise CanonicalReconciliationJobExecutionError(
                "El directorio reconciliation está fuera de pipeline runs."
            ) from exc
