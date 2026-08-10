from __future__ import annotations

from typing import Any

from src.api.schemas.structural_review import (
    StructuralReviewActionResponse,
    StructuralReviewItemDetail,
    StructuralReviewItemSummary,
)
from src.database.models import KnowledgeItem
from src.database.services import EffectiveKnowledgeService


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value))


def item_summary(item: KnowledgeItem) -> StructuralReviewItemSummary:
    version = item.knowledge_version
    return StructuralReviewItemSummary(
        id=str(item.id),
        canonical_id=item.canonical_id,
        entity_type=item.entity_type,
        parent_canonical_id=item.parent_canonical_id,
        title=item.title,
        route=item.route,
        current_review_status=item.current_review_status,
        generated_review_status=item.generated_review_status,
        review_revision=item.review_revision,
        knowledge_version_id=str(item.knowledge_version_id),
        knowledge_version=version.knowledge_version,
        version_status=_enum(version.status),
        content_hash=item.content_hash,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def item_detail(item: KnowledgeItem, session) -> StructuralReviewItemDetail:
    described = EffectiveKnowledgeService(session).describe(item.id)
    actions = item.review_actions
    return StructuralReviewItemDetail(
        **item_summary(item).model_dump(),
        source_payload=described["source_payload"],
        corrected_payload=described["corrected_payload"],
        effective_payload=described["effective_payload"],
        was_corrected=described["was_corrected"],
        review_history=tuple(
            StructuralReviewActionResponse(
                action=_enum(action.action),
                previous_status=action.previous_status,
                new_status=action.new_status,
                source=_enum(action.source),
                reviewer_id=action.reviewer_subject,
                reason=action.review_notes,
                corrected_payload=action.corrected_payload,
                created_at=action.created_at,
            )
            for action in actions
        ),
    )
