from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from src.database.repositories import KnowledgeRepository, ReviewRepository
from src.knowledge.canonical.enums import ReviewStatus

from .payloads import review_action_payload


class EffectiveKnowledgeService:
    def __init__(self, session: Session):
        self.knowledge = KnowledgeRepository(session)
        self.reviews = ReviewRepository(session)

    def describe(self, item_id):
        item = self.knowledge.get_item(item_id)
        if not item:
            raise LookupError("KnowledgeItem no encontrado")
        correction = self.reviews.latest_correction(item.id)
        source = copy.deepcopy(item.source_payload)
        corrected = copy.deepcopy(correction.corrected_payload) if correction else None
        return {
            "source_payload": source,
            "corrected_payload": corrected,
            "effective_payload": corrected or source,
            "was_corrected": corrected is not None,
            "history": [review_action_payload(action) for action in self.reviews.history(item.id)],
        }

    def list_approved(self, *, version_id=None):
        result = []
        for status in (ReviewStatus.APPROVED, ReviewStatus.CORRECTED):
            result.extend(self.knowledge.list_items(version_id=version_id, status=status, limit=1000))
        return result

    def projection_for_sync(self, *, version_id):
        return [
            {
                "canonical_id": item.canonical_id,
                "entity_type": item.entity_type,
                "content_hash": item.content_hash,
                "payload": self.describe(item.id)["effective_payload"],
            }
            for item in self.list_approved(version_id=version_id)
        ]

    def export_effective(self, *, version_id):
        grouped = {}
        for item in self.list_approved(version_id=version_id):
            grouped.setdefault(item.entity_type, []).append(
                self.describe(item.id)["effective_payload"]
            )
        return grouped
