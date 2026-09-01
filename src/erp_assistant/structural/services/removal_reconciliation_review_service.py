from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobStatus,
    RemovalReconciliationDecisionType,
    RemovalReviewActionType,
    ReviewSource,
)
from erp_assistant.persistence.postgres.models import (
    KnowledgeVersionRecord,
    PipelineJob,
    RemovalReconciliationDecisionRecord,
    RemovalReconciliationReviewAction,
    RemovalReconciliationReviewSet,
)
from erp_assistant.structural.canonical.ids import content_hash

from .removal_reconciliation_plan_service import (
    RemovalReconciliationDecision,
    RemovalReconciliationPlan,
    RemovalReconciliationPlanError,
    RemovalReconciliationPlanService,
)
from .structural_review_package_service import StructuralReviewPackageError
from .version_diff_service import VersionDiffError


class RemovalReconciliationReviewError(ValueError):
    pass


class RemovalReconciliationReviewNotPreparedError(RemovalReconciliationReviewError):
    pass


@dataclass(frozen=True)
class RemovalReviewDecisionState:
    id: str
    entity_type: str
    canonical_id: str
    active_item_id: str
    candidate_item_id: str | None
    screen_id: str | None
    plan_reason: str
    removal_confirmation: str | None
    proposed_decision: str
    current_decision: str | None
    requires_human_review: bool
    review_revision: int
    decision_fingerprint: str


@dataclass(frozen=True)
class RemovalReviewSetState:
    id: str
    candidate_version_id: str
    candidate_knowledge_version: str
    active_version_id: str
    active_knowledge_version: str
    erp_id: str
    candidate_origin: str
    raw_diff_totals: dict[str, int]
    plan_hash: str
    decision_count: int
    pending_review: int
    retain_from_active: int
    confirmed_remove: int
    decisions: tuple[RemovalReviewDecisionState, ...]


class RemovalReconciliationReviewService:
    """Persist and govern human decisions for raw VersionDiff removals."""

    def __init__(self, session: Session):
        self.session = session

    def prepare(self, candidate_version_id: uuid.UUID | str) -> RemovalReviewSetState:
        plan = self._plan(candidate_version_id)
        candidate, active = self._locked_versions(plan)
        existing = self.session.scalar(
            select(RemovalReconciliationReviewSet)
            .where(RemovalReconciliationReviewSet.candidate_version_id == candidate.id)
            .with_for_update()
        )
        if existing is not None:
            self._validate_set(existing, plan)
            return self._state(existing)

        plan_hash = self._plan_hash(plan)
        review_set = RemovalReconciliationReviewSet(
            candidate_version_id=candidate.id,
            active_version_id=active.id,
            erp_id=candidate.erp_id,
            candidate_knowledge_version=candidate.knowledge_version,
            active_knowledge_version=active.knowledge_version,
            candidate_origin=plan.candidate_origin,
            raw_diff_totals=dict(plan.raw_diff_totals),
            plan_hash=plan_hash,
            decision_count=plan.removal_total,
        )
        self.session.add(review_set)
        self.session.flush()
        for decision in plan.decisions:
            if decision.active_item_id is None:
                raise RemovalReconciliationReviewError(
                    "Un REMOVED gobernado debe conservar active_item_id."
                )
            record = RemovalReconciliationDecisionRecord(
                review_set_id=review_set.id,
                active_item_id=self._uuid(decision.active_item_id, "active_item_id"),
                candidate_item_id=(
                    self._uuid(decision.candidate_item_id, "candidate_item_id")
                    if decision.candidate_item_id is not None
                    else None
                ),
                entity_type=decision.entity_type,
                canonical_id=decision.canonical_id,
                screen_id=decision.screen_id,
                plan_reason=decision.reason,
                removal_confirmation=decision.removal_confirmation,
                proposed_decision=decision.decision,
                current_decision=None,
                requires_human_review=decision.requires_human_review,
                decision_fingerprint=self._decision_fingerprint(plan_hash, decision),
            )
            self.session.add(record)
        self.session.flush()
        self.session.refresh(review_set)
        self._validate_set(review_set, plan)
        return self._state(review_set)

    def get(self, candidate_version_id: uuid.UUID | str) -> RemovalReviewSetState:
        plan = self._plan(candidate_version_id)
        review_set = self._review_set(plan.candidate_version_id)
        if review_set is None:
            raise RemovalReconciliationReviewNotPreparedError(
                "Las decisiones de removal todavía no fueron preparadas para revisión."
            )
        self._ensure_versions_still_reviewable(review_set)
        self._validate_set(review_set, plan)
        return self._state(review_set)

    def confirm_retain(
        self,
        decision_id: uuid.UUID | str,
        *,
        reviewer: str,
        reason: str,
        expected_revision: int,
        source: ReviewSource = ReviewSource.CLI,
    ) -> RemovalReviewDecisionState:
        return self._change(
            decision_id,
            action=RemovalReviewActionType.CONFIRM_RETAIN,
            new_decision=RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE,
            reviewer=reviewer,
            reason=reason,
            expected_revision=expected_revision,
            source=source,
        )

    def confirm_remove(
        self,
        decision_id: uuid.UUID | str,
        *,
        reviewer: str,
        reason: str,
        expected_revision: int,
        source: ReviewSource = ReviewSource.CLI,
    ) -> RemovalReviewDecisionState:
        return self._change(
            decision_id,
            action=RemovalReviewActionType.CONFIRM_REMOVE,
            new_decision=RemovalReconciliationDecisionType.CONFIRMED_REMOVE,
            reviewer=reviewer,
            reason=reason,
            expected_revision=expected_revision,
            source=source,
        )

    def reset_to_pending(
        self,
        decision_id: uuid.UUID | str,
        *,
        reviewer: str,
        reason: str,
        expected_revision: int,
        source: ReviewSource = ReviewSource.CLI,
    ) -> RemovalReviewDecisionState:
        return self._change(
            decision_id,
            action=RemovalReviewActionType.RESET_TO_PENDING,
            new_decision=None,
            reviewer=reviewer,
            reason=reason,
            expected_revision=expected_revision,
            source=source,
        )

    def history(
        self, decision_id: uuid.UUID | str
    ) -> tuple[RemovalReconciliationReviewAction, ...]:
        decision_uuid = self._uuid(decision_id, "decision_id")
        if self.session.get(RemovalReconciliationDecisionRecord, decision_uuid) is None:
            raise LookupError("Removal decision no encontrada.")
        return tuple(
            self.session.scalars(
                select(RemovalReconciliationReviewAction)
                .where(RemovalReconciliationReviewAction.decision_id == decision_uuid)
                .order_by(
                    RemovalReconciliationReviewAction.created_at,
                    RemovalReconciliationReviewAction.id,
                )
            )
        )

    def resolved_plan(self, candidate_version_id: uuid.UUID | str) -> RemovalReconciliationPlan:
        plan = self._plan(candidate_version_id)
        if plan.removal_total == 0:
            return plan
        review_set = self._review_set(plan.candidate_version_id)
        if review_set is None:
            raise RemovalReconciliationReviewNotPreparedError(
                "Canonical reconciliation requiere Removal HITL preparado."
            )
        self._ensure_versions_still_reviewable(review_set)
        records = self._validate_set(review_set, plan, for_update=True)
        effective: list[RemovalReconciliationDecision] = []
        for source_decision in plan.decisions:
            record = records[(source_decision.entity_type, source_decision.canonical_id)]
            current = record.current_decision
            if record.requires_human_review and current is None:
                raise RemovalReconciliationReviewError(
                    "Canonical reconciliation requiere resolver todas las decisiones de removal."
                )
            current = current or record.proposed_decision
            if current == RemovalReconciliationDecisionType.UNRESOLVED:
                raise RemovalReconciliationReviewError(
                    "Una decisión de removal permanece UNRESOLVED."
                )
            action = self._validated_latest_action(record)
            if record.requires_human_review and (action is None or action.new_decision != current):
                raise RemovalReconciliationReviewError(
                    "La decisión resuelta no conserva una acción humana gobernada."
                )
            effective.append(
                replace(
                    source_decision,
                    decision=current,
                    requires_human_review=False,
                    review_set_id=str(review_set.id),
                    review_decision_id=str(record.id),
                    review_action_id=str(action.id) if action is not None else None,
                    review_revision=record.review_revision,
                )
            )
        retained = sum(
            value.decision == RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE
            for value in effective
        )
        removed = sum(
            value.decision == RemovalReconciliationDecisionType.CONFIRMED_REMOVE
            for value in effective
        )
        return replace(
            plan,
            retain_from_active_total=retained,
            confirmed_removed_total=removed,
            unresolved_total=0,
            decisions=tuple(effective),
        )

    def _change(
        self,
        decision_id: uuid.UUID | str,
        *,
        action: RemovalReviewActionType,
        new_decision: RemovalReconciliationDecisionType | None,
        reviewer: str,
        reason: str,
        expected_revision: int,
        source: ReviewSource,
    ) -> RemovalReviewDecisionState:
        reviewer = " ".join(str(reviewer or "").split())
        reason = " ".join(str(reason or "").split())
        if not reviewer:
            raise RemovalReconciliationReviewError("reviewer no puede estar vacío.")
        if not reason:
            raise RemovalReconciliationReviewError("La decisión requiere una justificación.")
        decision_uuid = self._uuid(decision_id, "decision_id")
        record = self.session.scalar(
            select(RemovalReconciliationDecisionRecord)
            .where(RemovalReconciliationDecisionRecord.id == decision_uuid)
            .with_for_update()
        )
        if record is None:
            raise LookupError("Removal decision no encontrada.")
        plan = self._plan(record.review_set.candidate_version_id)
        records = self._validate_set(record.review_set, plan)
        record = records[(record.entity_type, record.canonical_id)]
        self._ensure_versions_still_reviewable(record.review_set)
        self._ensure_not_consumed(record.review_set)
        if record.review_revision != expected_revision:
            raise RemovalReconciliationReviewError("Conflicto de revisión concurrente.")
        previous = record.current_decision
        if action == RemovalReviewActionType.RESET_TO_PENDING:
            if previous is None:
                raise RemovalReconciliationReviewError(
                    "Transición no permitida: la decisión ya está pending."
                )
        elif previous is not None:
            raise RemovalReconciliationReviewError(
                "Transición no permitida: resetee la decisión antes de cambiarla."
            )
        if new_decision == RemovalReconciliationDecisionType.UNRESOLVED:
            raise RemovalReconciliationReviewError("UNRESOLVED no es una decisión humana final.")
        self._validated_latest_action(record)
        review_action = RemovalReconciliationReviewAction(
            decision_id=record.id,
            action=action,
            previous_decision=previous,
            new_decision=new_decision,
            review_notes=reason[:4000],
            reviewer_subject=reviewer[:240],
            source=ReviewSource(source),
            decision_fingerprint=record.decision_fingerprint,
        )
        self.session.add(review_action)
        record.current_decision = new_decision
        record.review_revision += 1
        self.session.flush()
        return self._decision_state(record)

    def _plan(self, candidate_version_id) -> RemovalReconciliationPlan:
        try:
            return RemovalReconciliationPlanService(self.session).build(candidate_version_id)
        except (
            RemovalReconciliationPlanError,
            StructuralReviewPackageError,
            VersionDiffError,
        ) as exc:
            raise RemovalReconciliationReviewError(str(exc)) from exc

    def _locked_versions(
        self, plan: RemovalReconciliationPlan
    ) -> tuple[KnowledgeVersionRecord, KnowledgeVersionRecord]:
        candidate = self.session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.id == self._uuid(plan.candidate_version_id, "candidate"))
            .with_for_update()
        )
        active = self.session.scalar(
            select(KnowledgeVersionRecord)
            .where(KnowledgeVersionRecord.id == self._uuid(plan.active_version_id, "active"))
            .with_for_update()
        )
        if candidate is None or active is None:
            raise RemovalReconciliationReviewError("Candidate o ACTIVE ya no existe.")
        if (
            candidate.status != KnowledgeVersionStatus.IMPORTED
            or active.status != KnowledgeVersionStatus.ACTIVE
            or candidate.erp_id != active.erp_id
        ):
            raise RemovalReconciliationReviewError(
                "Candidate/ACTIVE cambiaron durante la preparación de Removal HITL."
            )
        if (
            candidate.knowledge_version != plan.candidate_knowledge_version
            or active.knowledge_version != plan.active_knowledge_version
        ):
            raise RemovalReconciliationReviewError(
                "KnowledgeVersion pin cambió durante Removal HITL."
            )
        return candidate, active

    def _ensure_versions_still_reviewable(self, review_set: RemovalReconciliationReviewSet) -> None:
        candidate = self.session.get(KnowledgeVersionRecord, review_set.candidate_version_id)
        active = self.session.get(KnowledgeVersionRecord, review_set.active_version_id)
        if (
            candidate is None
            or active is None
            or candidate.status != KnowledgeVersionStatus.IMPORTED
            or active.status != KnowledgeVersionStatus.ACTIVE
            or candidate.erp_id != review_set.erp_id
            or active.erp_id != review_set.erp_id
            or candidate.knowledge_version != review_set.candidate_knowledge_version
            or active.knowledge_version != review_set.active_knowledge_version
        ):
            raise RemovalReconciliationReviewError(
                "El contexto RAW/ACTIVE del Removal HITL ya no es vigente."
            )

    def _ensure_not_consumed(self, review_set: RemovalReconciliationReviewSet) -> None:
        jobs = self.session.scalars(
            select(PipelineJob).where(
                PipelineJob.kind == PipelineJobKind.CANONICAL_RECONCILIATION,
                PipelineJob.status == PipelineJobStatus.SUCCEEDED,
                PipelineJob.knowledge_version_id == review_set.candidate_version_id,
            )
        )
        review_set_id = str(review_set.id)
        for job in jobs:
            payload = dict(job.result_payload or {})
            decisions = payload.get("decisions")
            if isinstance(decisions, list) and any(
                isinstance(value, dict) and value.get("review_set_id") == review_set_id
                for value in decisions
            ):
                raise RemovalReconciliationReviewError(
                    "Transición no permitida: Removal HITL ya fue consumido por una "
                    "canonical_reconciliation exitosa."
                )

    def _review_set(
        self, candidate_version_id, *, for_update: bool = False
    ) -> RemovalReconciliationReviewSet | None:
        statement = select(RemovalReconciliationReviewSet).where(
            RemovalReconciliationReviewSet.candidate_version_id
            == self._uuid(candidate_version_id, "candidate_version_id")
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def _validate_set(
        self,
        review_set: RemovalReconciliationReviewSet,
        plan: RemovalReconciliationPlan,
        *,
        for_update: bool = False,
    ) -> dict[tuple[str, str], RemovalReconciliationDecisionRecord]:
        plan_hash = self._plan_hash(plan)
        candidate = self.session.get(
            KnowledgeVersionRecord, self._uuid(plan.candidate_version_id, "candidate_version_id")
        )
        active = self.session.get(
            KnowledgeVersionRecord, self._uuid(plan.active_version_id, "active_version_id")
        )
        if candidate is None or active is None or candidate.erp_id != active.erp_id:
            raise RemovalReconciliationReviewError("Candidate/ACTIVE del review set son inválidos.")
        expected_set = {
            "candidate_version_id": plan.candidate_version_id,
            "active_version_id": plan.active_version_id,
            "candidate_knowledge_version": plan.candidate_knowledge_version,
            "active_knowledge_version": plan.active_knowledge_version,
            "candidate_origin": plan.candidate_origin,
            "erp_id": candidate.erp_id,
            "raw_diff_totals": plan.raw_diff_totals,
            "plan_hash": plan_hash,
            "decision_count": plan.removal_total,
        }
        actual_set = {
            "candidate_version_id": str(review_set.candidate_version_id),
            "active_version_id": str(review_set.active_version_id),
            "candidate_knowledge_version": review_set.candidate_knowledge_version,
            "active_knowledge_version": review_set.active_knowledge_version,
            "candidate_origin": review_set.candidate_origin,
            "erp_id": review_set.erp_id,
            "raw_diff_totals": dict(review_set.raw_diff_totals),
            "plan_hash": review_set.plan_hash,
            "decision_count": review_set.decision_count,
        }
        if actual_set != expected_set:
            raise RemovalReconciliationReviewError(
                "Removal review set no coincide con el plan RAW actual."
            )
        records_statement = select(RemovalReconciliationDecisionRecord).where(
            RemovalReconciliationDecisionRecord.review_set_id == review_set.id
        )
        if for_update:
            records_statement = records_statement.with_for_update()
        records = list(self.session.scalars(records_statement))
        if len(records) != plan.removal_total:
            raise RemovalReconciliationReviewError(
                "Removal review set no contiene exactamente un registro por REMOVED."
            )
        by_key = {(value.entity_type, value.canonical_id): value for value in records}
        if len(by_key) != len(records):
            raise RemovalReconciliationReviewError(
                "Removal review contiene identidades duplicadas."
            )
        for decision in plan.decisions:
            key = (decision.entity_type, decision.canonical_id)
            record = by_key.get(key)
            if record is None:
                raise RemovalReconciliationReviewError("Falta una removal decision persistida.")
            expected = {
                "active_item_id": decision.active_item_id,
                "candidate_item_id": decision.candidate_item_id,
                "screen_id": decision.screen_id,
                "plan_reason": decision.reason,
                "removal_confirmation": decision.removal_confirmation,
                "proposed_decision": decision.decision.value,
                "requires_human_review": decision.requires_human_review,
                "decision_fingerprint": self._decision_fingerprint(plan_hash, decision),
            }
            actual = {
                "active_item_id": str(record.active_item_id),
                "candidate_item_id": (
                    str(record.candidate_item_id) if record.candidate_item_id else None
                ),
                "screen_id": record.screen_id,
                "plan_reason": record.plan_reason,
                "removal_confirmation": record.removal_confirmation,
                "proposed_decision": record.proposed_decision.value,
                "requires_human_review": record.requires_human_review,
                "decision_fingerprint": record.decision_fingerprint,
            }
            if actual != expected:
                raise RemovalReconciliationReviewError(
                    "Removal decision persistida no coincide con su provenance RAW."
                )
        return by_key

    @staticmethod
    def _plan_hash(plan: RemovalReconciliationPlan) -> str:
        payload = {
            "candidate_version_id": plan.candidate_version_id,
            "candidate_knowledge_version": plan.candidate_knowledge_version,
            "active_version_id": plan.active_version_id,
            "active_knowledge_version": plan.active_knowledge_version,
            "candidate_origin": plan.candidate_origin,
            "raw_diff_totals": dict(plan.raw_diff_totals),
            "decisions": [
                {
                    "entity_type": value.entity_type,
                    "canonical_id": value.canonical_id,
                    "active_item_id": value.active_item_id,
                    "candidate_item_id": value.candidate_item_id,
                    "screen_id": value.screen_id,
                    "reason": value.reason,
                    "decision": value.decision.value,
                    "removal_confirmation": value.removal_confirmation,
                    "requires_human_review": value.requires_human_review,
                }
                for value in plan.decisions
            ],
        }
        return content_hash(payload)

    @staticmethod
    def _decision_fingerprint(plan_hash: str, decision: RemovalReconciliationDecision) -> str:
        return content_hash(
            {
                "plan_hash": plan_hash,
                "entity_type": decision.entity_type,
                "canonical_id": decision.canonical_id,
                "active_item_id": decision.active_item_id,
                "candidate_item_id": decision.candidate_item_id,
                "screen_id": decision.screen_id,
                "reason": decision.reason,
                "decision": decision.decision.value,
                "removal_confirmation": decision.removal_confirmation,
                "requires_human_review": decision.requires_human_review,
            }
        )

    def _state(self, review_set: RemovalReconciliationReviewSet) -> RemovalReviewSetState:
        decisions = tuple(self._decision_state(value) for value in review_set.decisions)
        return RemovalReviewSetState(
            id=str(review_set.id),
            candidate_version_id=str(review_set.candidate_version_id),
            candidate_knowledge_version=review_set.candidate_knowledge_version,
            active_version_id=str(review_set.active_version_id),
            active_knowledge_version=review_set.active_knowledge_version,
            erp_id=review_set.erp_id,
            candidate_origin=review_set.candidate_origin,
            raw_diff_totals=dict(review_set.raw_diff_totals),
            plan_hash=review_set.plan_hash,
            decision_count=review_set.decision_count,
            pending_review=sum(value.current_decision is None for value in decisions),
            retain_from_active=sum(
                value.current_decision == RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE
                for value in review_set.decisions
            ),
            confirmed_remove=sum(
                value.current_decision == RemovalReconciliationDecisionType.CONFIRMED_REMOVE
                for value in review_set.decisions
            ),
            decisions=decisions,
        )

    @staticmethod
    def _decision_state(record: RemovalReconciliationDecisionRecord) -> RemovalReviewDecisionState:
        return RemovalReviewDecisionState(
            id=str(record.id),
            entity_type=record.entity_type,
            canonical_id=record.canonical_id,
            active_item_id=str(record.active_item_id),
            candidate_item_id=str(record.candidate_item_id) if record.candidate_item_id else None,
            screen_id=record.screen_id,
            plan_reason=record.plan_reason,
            removal_confirmation=record.removal_confirmation,
            proposed_decision=record.proposed_decision.value,
            current_decision=record.current_decision.value if record.current_decision else None,
            requires_human_review=record.requires_human_review,
            review_revision=record.review_revision,
            decision_fingerprint=record.decision_fingerprint,
        )

    def _validated_latest_action(
        self, record: RemovalReconciliationDecisionRecord
    ) -> RemovalReconciliationReviewAction | None:
        actions = list(
            self.session.scalars(
                select(RemovalReconciliationReviewAction)
                .where(RemovalReconciliationReviewAction.decision_id == record.id)
                .order_by(
                    RemovalReconciliationReviewAction.created_at,
                    RemovalReconciliationReviewAction.id,
                )
            )
        )
        if len(actions) != record.review_revision:
            raise RemovalReconciliationReviewError(
                "El historial de removal review no coincide con review_revision."
            )
        expected_previous = None
        for action in actions:
            if action.decision_fingerprint != record.decision_fingerprint:
                raise RemovalReconciliationReviewError(
                    "La acción de removal review no coincide con la decisión persistida."
                )
            if action.previous_decision != expected_previous:
                raise RemovalReconciliationReviewError(
                    "La secuencia histórica de removal review es inconsistente."
                )
            expected_previous = action.new_decision
        if expected_previous != record.current_decision:
            raise RemovalReconciliationReviewError(
                "El historial de removal review no explica la decisión actual."
            )
        return actions[-1] if actions else None

    @staticmethod
    def _uuid(value: Any, field: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise RemovalReconciliationReviewError(f"{field} inválido.") from exc
