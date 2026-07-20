from __future__ import annotations

import copy
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.database.enums import ReviewActionType, ReviewSource
from src.database.models import KnowledgeItem, ReviewAction
from src.database.repositories import KnowledgeRepository, ReviewRepository
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.models import (
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

    def approve(self, item_id, *, reviewer=None, notes=None, expected_revision=None):
        return self._change(
            item_id, ReviewActionType.APPROVE, ReviewStatus.APPROVED,
            reviewer=reviewer, notes=notes, expected_revision=expected_revision
        )

    def reject(self, item_id, *, reviewer=None, notes=None, expected_revision=None):
        if not notes:
            raise ValueError("El rechazo requiere notas")
        return self._change(
            item_id, ReviewActionType.REJECT, ReviewStatus.REJECTED,
            reviewer=reviewer, notes=notes, expected_revision=expected_revision
        )

    def correct(
        self, item_id, corrected_payload, *, reviewer=None, notes=None, expected_revision=None
    ):
        if not notes:
            raise ValueError("La corrección requiere notas")
        item = self._locked(item_id, expected_revision)
        payload = validate_safe_json(corrected_payload)
        if payload.get("id") != item.canonical_id:
            raise ValueError("La corrección debe mantener el canonical_id original")
        original = item.source_payload
        for key in ("erp_id", "module_id", "screen_id", "table_id", "source_state_id",
                    "target_state_id", "event_id"):
            if original.get(key) != payload.get(key):
                raise ValueError(f"La relación crítica {key} no puede modificarse")
        model = MODELS.get(item.entity_type)
        if model:
            try:
                model.model_validate(payload)
            except ValidationError as exc:
                raise ValueError("La corrección no valida contra el modelo canónico") from exc
        return self._record(
            item, ReviewActionType.CORRECT, ReviewStatus.CORRECTED,
            reviewer, notes, payload
        )

    def reset_to_pending(
        self, item_id, *, reviewer=None, notes=None, expected_revision=None
    ):
        item = self._locked(item_id, expected_revision)
        return self._record(
            item, ReviewActionType.RESET_TO_PENDING, ReviewStatus.PENDING_REVIEW,
            reviewer, notes, None, allow_any=True
        )

    def get_review_history(self, item_id):
        self.get_item(item_id)
        return self.reviews.history(item_id)

    def get_effective_payload(self, item_id):
        item = self.get_item(item_id)
        correction = self.reviews.latest_correction(item.id)
        return copy.deepcopy(
            correction.corrected_payload if correction else item.source_payload
        )

    def _change(self, item_id, action, status, *, reviewer, notes, expected_revision):
        item = self._locked(item_id, expected_revision)
        return self._record(item, action, status, reviewer, notes, None)

    def _locked(self, item_id, expected_revision):
        item = self.knowledge.get_item(item_id, for_update=True)
        if not item:
            raise LookupError("KnowledgeItem no encontrado")
        if expected_revision is not None and item.review_revision != expected_revision:
            raise ValueError("Conflicto de revisión concurrente")
        return item

    def _record(
        self, item, action, status, reviewer, notes, corrected_payload, allow_any=False
    ):
        previous = item.current_review_status
        if not allow_any and status not in TRANSITIONS.get(previous, set()):
            raise ValueError(f"Transición no permitida: {previous} -> {status}")
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
                source=ReviewSource.CLI,
            )
        )
        item.current_review_status = status
        item.review_revision += 1
        self.session.flush()
        return item

