from __future__ import annotations

import copy

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import (
    KnowledgeVersionStatus,
    ReviewActionType,
    ReviewSource,
    SyncStatus,
)
from erp_assistant.persistence.postgres.models import KnowledgeItem, ReviewAction, SyncJob
from erp_assistant.persistence.postgres.repositories import KnowledgeRepository, ReviewRepository
from erp_assistant.persistence.postgres.types import utcnow
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.canonical.models import (
    Control,
    ERPSystem,
    Event,
    Evidence,
    FieldEntity,
    Link,
    Module,
    Screen,
    Table,
    TableColumn,
    Transition,
    UIState,
)

from .payloads import validate_safe_json

MODELS = {
    "erp_system": ERPSystem,
    "module": Module,
    "screen": Screen,
    "ui_state": UIState,
    "field": FieldEntity,
    "control": Control,
    "table": Table,
    "table_column": TableColumn,
    "link": Link,
    "event": Event,
    "transition": Transition,
    "evidence": Evidence,
}
TRANSITIONS = {
    ReviewStatus.PENDING_REVIEW: {
        ReviewStatus.APPROVED,
        ReviewStatus.REJECTED,
        ReviewStatus.CORRECTED,
    },
    ReviewStatus.APPROVED: {ReviewStatus.CORRECTED, ReviewStatus.REJECTED},
    ReviewStatus.REJECTED: {ReviewStatus.PENDING_REVIEW},
    ReviewStatus.CORRECTED: {ReviewStatus.APPROVED, ReviewStatus.REJECTED},
}
PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


class KnowledgeReviewService:
    def __init__(self, session: Session):
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.reviews = ReviewRepository(session)

    def list_items(self, **filters):
        return self.knowledge.list_items(**filters)

    def get_item(self, item_id):
        item = self.knowledge.get_item(item_id)
        if not item:
            raise LookupError("KnowledgeItem no encontrado")
        return item

    def approve(
        self, item_id, *, reviewer=None, notes=None, expected_revision=None, source=ReviewSource.CLI
    ):
        return self._change(
            item_id,
            ReviewActionType.APPROVE,
            ReviewStatus.APPROVED,
            reviewer=reviewer,
            notes=notes,
            expected_revision=expected_revision,
            source=source,
        )

    def reject(
        self, item_id, *, reviewer=None, notes=None, expected_revision=None, source=ReviewSource.CLI
    ):
        if not notes:
            raise ValueError("El rechazo requiere notas")
        return self._change(
            item_id,
            ReviewActionType.REJECT,
            ReviewStatus.REJECTED,
            reviewer=reviewer,
            notes=notes,
            expected_revision=expected_revision,
            source=source,
        )

    def correct(
        self,
        item_id,
        corrected_payload,
        *,
        reviewer=None,
        notes=None,
        expected_revision=None,
        source=ReviewSource.CLI,
    ):
        if not notes:
            raise ValueError("La corrección requiere notas")
        item = self._locked(item_id, expected_revision)
        payload = validate_safe_json(corrected_payload)
        if payload.get("id") != item.canonical_id:
            raise ValueError("La corrección debe mantener el canonical_id original")
        original = item.source_payload
        for key in (
            "erp_id",
            "parent_module_id",
            "module_id",
            "screen_id",
            "table_id",
            "source_state_id",
            "target_state_id",
            "event_id",
        ):
            if original.get(key) != payload.get(key):
                raise ValueError(f"La relación crítica {key} no puede modificarse")
        model = MODELS.get(item.entity_type)
        if model:
            try:
                model.model_validate(payload)
            except ValidationError as exc:
                raise ValueError("La corrección no valida contra el modelo canónico") from exc
        return self._record(
            item,
            ReviewActionType.CORRECT,
            ReviewStatus.CORRECTED,
            reviewer,
            notes,
            payload,
            source=source,
        )

    def reset_to_pending(
        self, item_id, *, reviewer=None, notes=None, expected_revision=None, source=ReviewSource.CLI
    ):
        item = self._locked(item_id, expected_revision)
        return self._record(
            item,
            ReviewActionType.RESET_TO_PENDING,
            ReviewStatus.PENDING_REVIEW,
            reviewer,
            notes,
            None,
            allow_any=True,
            source=source,
        )

    def get_review_history(self, item_id):
        self.get_item(item_id)
        return self.reviews.history(item_id)

    def get_effective_payload(self, item_id):
        item = self.get_item(item_id)
        correction = self.reviews.latest_correction(item.id)
        return copy.deepcopy(correction.corrected_payload if correction else item.source_payload)

    def _change(self, item_id, action, status, *, reviewer, notes, expected_revision, source):
        item = self._locked(item_id, expected_revision)
        return self._record(item, action, status, reviewer, notes, None, source=source)

    def _locked(self, item_id, expected_revision):
        item = self.knowledge.get_item(item_id, for_update=True)
        if not item:
            raise LookupError("KnowledgeItem no encontrado")
        if expected_revision is not None and item.review_revision != expected_revision:
            raise ValueError("Conflicto de revisión concurrente")
        return item

    def _record(
        self,
        item,
        action,
        status,
        reviewer,
        notes,
        corrected_payload,
        allow_any=False,
        source=ReviewSource.CLI,
    ):
        previous = item.current_review_status
        if not allow_any and status not in TRANSITIONS.get(previous, set()):
            raise ValueError(f"Transición no permitida: {previous} -> {status}")
        if previous in PUBLISHABLE or status in PUBLISHABLE:
            self._invalidate_structural_projection_jobs(item)
        self.session.add(
            ReviewAction(
                knowledge_item_id=item.id,
                action=action,
                previous_status=previous,
                new_status=status,
                corrected_payload=corrected_payload,
                review_notes=(notes or "")[:4000] or None,
                reviewer_subject=(reviewer or "")[:240] or None,
                item_content_hash=item.content_hash,
                source=ReviewSource(source),
            )
        )
        item.current_review_status = status
        item.review_revision += 1
        self.session.flush()
        return item

    def _invalidate_structural_projection_jobs(self, item: KnowledgeItem) -> None:
        version = item.knowledge_version
        if version.status != KnowledgeVersionStatus.ACTIVE:
            return
        jobs = list(
            self.session.scalars(
                select(SyncJob)
                .where(SyncJob.knowledge_version_id == item.knowledge_version_id)
                .order_by(SyncJob.target)
                .with_for_update()
            )
        )
        if any(job.status == SyncStatus.RUNNING for job in jobs):
            raise ValueError(
                "Conflicto de revisión concurrente: no se puede modificar conocimiento "
                "publicable mientras una proyección estructural está en ejecución"
            )
        requested_at = utcnow()
        for job in jobs:
            job.status = SyncStatus.PENDING
            job.requested_at = requested_at
            job.started_at = None
            job.finished_at = None
            job.checkpoint = None
            job.error_summary = None
