from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import (
    PipelineJobKind,
    PipelineJobStatus,
    RemovalReconciliationDecisionType,
)
from src.database.models import KnowledgeVersionRecord, PipelineJob

from .structural_review_package_service import (
    StructuralReviewPackageService,
)
from .version_diff_service import VersionDiffChangeType, VersionDiffService


class RemovalReconciliationPlanError(ValueError):
    pass


@dataclass(frozen=True)
class RemovalReconciliationDecision:
    entity_type: str
    canonical_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    screen_id: str | None
    reason: str
    decision: RemovalReconciliationDecisionType
    removal_confirmation: str | None
    requires_human_review: bool
    review_set_id: str | None = None
    review_decision_id: str | None = None
    review_action_id: str | None = None
    review_revision: int | None = None


@dataclass(frozen=True)
class RemovalReconciliationPlan:
    candidate_version_id: str
    candidate_knowledge_version: str
    active_version_id: str
    active_knowledge_version: str
    candidate_origin: str
    raw_diff_totals: dict[str, int]
    removal_total: int
    retain_from_active_total: int
    confirmed_removed_total: int
    unresolved_total: int
    decisions: tuple[RemovalReconciliationDecision, ...]


class RemovalReconciliationPlanService:
    """Read-only conservative plan for raw VersionDiff REMOVED observations."""

    def __init__(self, session: Session):
        self.session = session

    def build(self, candidate_version_id: uuid.UUID | str) -> RemovalReconciliationPlan:
        diff = VersionDiffService(self.session).compare(candidate_version_id)
        package = StructuralReviewPackageService(self.session).build(candidate_version_id)
        if (
            package.active_version_id != diff.active_version_id
            or package.candidate_version_id != diff.candidate_version_id
            or package.erp_id != diff.erp_id
            or package.diff_totals != diff.totals
        ):
            raise RemovalReconciliationPlanError(
                "VersionDiff y Structural Review Package son inconsistentes."
            )
        candidate = self.session.get(KnowledgeVersionRecord, uuid.UUID(diff.candidate_version_id))
        active = self.session.get(KnowledgeVersionRecord, uuid.UUID(diff.active_version_id))
        if candidate is None or active is None or candidate.erp_id != active.erp_id:
            raise RemovalReconciliationPlanError("Candidate y ACTIVE no corresponden al mismo ERP.")
        if candidate.id == active.id:
            raise RemovalReconciliationPlanError("Candidate y ACTIVE deben ser distintos.")
        if package.candidate_origin in {"partial_module_merge", "partial_screen_merge"}:
            self._validate_partial_base(candidate, active, package.candidate_origin)
        elif package.candidate_origin != "full_canonical":
            raise RemovalReconciliationPlanError("candidate_origin no reconocido.")

        changes = self._removed_changes(package)
        decisions = []
        for item in diff.items:
            if item.change_type != VersionDiffChangeType.REMOVED:
                continue
            key = (item.entity_type, item.canonical_id)
            scoped = changes.get(key)
            if scoped is None:
                raise RemovalReconciliationPlanError(
                    "Un REMOVED del VersionDiff no pudo correlacionarse con "
                    "Structural Review Package."
                )
            screen_id, change = scoped
            confirmation = change.removal_confirmation
            if confirmation not in {None, "unconfirmed", "confirmed_removed"}:
                raise RemovalReconciliationPlanError("removal_confirmation no reconocido.")
            if package.candidate_origin in {"partial_module_merge", "partial_screen_merge"}:
                if confirmation != "unconfirmed" or not change.requires_removal_review:
                    raise RemovalReconciliationPlanError(
                        "REMOVED de partial merge no conserva estado unconfirmed gobernado."
                    )
                decision = RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE
                reason = (
                    "not_observed_in_partial_module_crawl"
                    if package.candidate_origin == "partial_module_merge"
                    else "not_observed_in_partial_screen_crawl"
                )
                review = True
            else:
                if confirmation != "unconfirmed" or not change.requires_removal_review:
                    raise RemovalReconciliationPlanError(
                        "REMOVED de FULL candidate no conserva estado unconfirmed gobernado."
                    )
                decision = RemovalReconciliationDecisionType.UNRESOLVED
                reason = "not_observed_in_full_crawl"
                review = True
            decisions.append(
                RemovalReconciliationDecision(
                    entity_type=item.entity_type,
                    canonical_id=item.canonical_id,
                    active_item_id=item.active_item_id,
                    candidate_item_id=item.candidate_item_id,
                    screen_id=screen_id,
                    reason=reason,
                    decision=decision,
                    removal_confirmation=confirmation,
                    requires_human_review=review,
                )
            )
        if len(decisions) != diff.totals["removed"]:
            raise RemovalReconciliationPlanError(
                "El plan no contiene exactamente un decision por REMOVED."
            )
        decisions.sort(key=lambda value: (value.entity_type, value.canonical_id))
        return RemovalReconciliationPlan(
            candidate_version_id=diff.candidate_version_id,
            candidate_knowledge_version=diff.candidate_knowledge_version,
            active_version_id=diff.active_version_id,
            active_knowledge_version=diff.active_knowledge_version,
            candidate_origin=package.candidate_origin,
            raw_diff_totals=diff.totals,
            removal_total=len(decisions),
            retain_from_active_total=sum(
                value.decision == RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE
                for value in decisions
            ),
            confirmed_removed_total=0,
            unresolved_total=sum(
                value.decision == RemovalReconciliationDecisionType.UNRESOLVED
                for value in decisions
            ),
            decisions=tuple(decisions),
        )

    def _validate_partial_base(self, candidate, active, candidate_origin: str) -> None:
        origin = self._origin_import(candidate)
        try:
            source_id = uuid.UUID(str((origin.parameters or {}).get("source_canonical_job_id")))
        except (TypeError, ValueError) as exc:
            raise RemovalReconciliationPlanError("La provenance merge fuente es inválida.") from exc
        source = self.session.get(PipelineJob, source_id)
        result = dict(source.result_payload or {}) if source else {}
        expected_scope = (
            "module" if candidate_origin == "partial_module_merge" else "screen"
        )
        if (
            source is None
            or source.kind != PipelineJobKind.CANONICAL_MERGE
            or source.status != PipelineJobStatus.SUCCEEDED
            or result.get("merged_from_scope") != expected_scope
            or str(result.get("base_knowledge_version_id") or "") != str(active.id)
            or result.get("base_knowledge_version") != active.knowledge_version
            or result.get("erp_id") != active.erp_id
        ):
            raise RemovalReconciliationPlanError(
                "La base ACTIVE del partial merge no coincide con provenance."
            )

    def _origin_import(self, candidate):
        jobs = list(
            self.session.scalars(
                select(PipelineJob).where(
                    PipelineJob.kind == PipelineJobKind.CANONICAL_IMPORT,
                    PipelineJob.status == PipelineJobStatus.SUCCEEDED,
                    PipelineJob.knowledge_version_id == candidate.id,
                )
            )
        )
        origins = [
            job for job in jobs if dict(job.result_payload or {}).get("import_result") == "imported"
        ]
        if len(origins) != 1:
            raise RemovalReconciliationPlanError(
                "La provenance canonical_import originaria es ausente o ambigua."
            )
        return origins[0]

    @staticmethod
    def _removed_changes(package):
        result = {}
        groups = [(value.screen_id, value.changes) for value in package.packages]
        groups.append((None, package.unscoped_changes))
        for screen_id, values in groups:
            for change in values:
                if change.change_type != "removed":
                    continue
                key = (change.entity_type, change.canonical_id)
                if key in result:
                    raise RemovalReconciliationPlanError(
                        "Structural Review Package contiene REMOVED duplicado."
                    )
                result[key] = (screen_id, change)
        return result
