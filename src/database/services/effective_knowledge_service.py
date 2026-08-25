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

    @staticmethod
    def _description(item, history):
        correction = ReviewRepository.latest_correction_from_history(history)
        source = copy.deepcopy(item.source_payload)
        corrected = copy.deepcopy(correction.corrected_payload) if correction else None
        return {
            "source_payload": source,
            "corrected_payload": corrected,
            "effective_payload": corrected or source,
            "was_corrected": corrected is not None,
            "history": [review_action_payload(action) for action in history],
        }

    def describe_item(self, item):
        history = self.reviews.history(item.id)
        return self._description(item, history)

    def describe(self, item_id):
        item = self.knowledge.get_item(item_id)
        if not item:
            raise LookupError("KnowledgeItem no encontrado")
        return self.describe_item(item)

    def describe_many(self, items):
        rows = list(items)
        histories = self.reviews.history_many([item.id for item in rows])
        return {
            item.id: self._description(item, histories.get(item.id, []))
            for item in rows
        }

    def list_approved(self, *, version_id=None):
        result = []
        for status in (ReviewStatus.APPROVED, ReviewStatus.CORRECTED):
            offset = 0
            while True:
                batch = self.knowledge.list_items(
                    version_id=version_id, status=status, limit=1000, offset=offset
                )
                result.extend(batch)
                if len(batch) < 1000:
                    break
                offset += len(batch)
        return result

    def projection_for_sync(self, *, version_id):
        items = self.list_approved(version_id=version_id)
        descriptions = self.describe_many(items)
        return [
            {
                "canonical_id": item.canonical_id,
                "entity_type": item.entity_type,
                "content_hash": item.content_hash,
                "review_status": str(item.current_review_status),
                "payload": descriptions[item.id]["effective_payload"],
            }
            for item in items
        ]

    def export_effective(self, *, version_id):
        items = self.list_approved(version_id=version_id)
        descriptions = self.describe_many(items)
        grouped = {}
        for item in items:
            grouped.setdefault(item.entity_type, []).append(
                descriptions[item.id]["effective_payload"]
            )
        return grouped
