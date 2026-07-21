from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import SemanticProposal
from src.knowledge.canonical.enums import ReviewStatus


class SemanticProposalRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, proposal: SemanticProposal) -> SemanticProposal:
        self.session.add(proposal)
        return proposal

    def get_by_id(
        self, proposal_id: uuid.UUID | str, *, for_update: bool = False
    ) -> SemanticProposal | None:
        try:
            normalized_id = uuid.UUID(str(proposal_id))
        except (TypeError, ValueError):
            return None
        query = select(SemanticProposal).where(SemanticProposal.id == normalized_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def lock_for_update(self, proposal_id: uuid.UUID | str) -> SemanticProposal | None:
        return self.get_by_id(proposal_id, for_update=True)

    def get_by_semantic_id(self, semantic_id: str) -> SemanticProposal | None:
        return self.session.scalar(
            select(SemanticProposal).where(SemanticProposal.semantic_id == semantic_id)
        )

    def get_by_generation_identity(
        self,
        *,
        knowledge_version_id: uuid.UUID,
        screen_knowledge_item_id: uuid.UUID,
        semantic_type: str,
        evidence_hash: str,
        prompt_hash: str,
        generation_model: str,
        generation_parameters_hash: str,
    ) -> SemanticProposal | None:
        return self.session.scalar(
            select(SemanticProposal).where(
                SemanticProposal.knowledge_version_id == knowledge_version_id,
                SemanticProposal.screen_knowledge_item_id == screen_knowledge_item_id,
                SemanticProposal.semantic_type == semantic_type,
                SemanticProposal.evidence_hash == evidence_hash,
                SemanticProposal.prompt_hash == prompt_hash,
                SemanticProposal.generation_model == generation_model,
                SemanticProposal.generation_parameters_hash == generation_parameters_hash,
            )
        )

    def list(
        self,
        *,
        knowledge_version_id: uuid.UUID | None = None,
        screen_knowledge_item_id: uuid.UUID | None = None,
        semantic_type: str | None = None,
        current_review_status: ReviewStatus | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SemanticProposal]:
        query = select(SemanticProposal)
        if knowledge_version_id is not None:
            query = query.where(SemanticProposal.knowledge_version_id == knowledge_version_id)
        if screen_knowledge_item_id is not None:
            query = query.where(
                SemanticProposal.screen_knowledge_item_id == screen_knowledge_item_id
            )
        if semantic_type is not None:
            query = query.where(SemanticProposal.semantic_type == semantic_type)
        if current_review_status is not None:
            query = query.where(
                SemanticProposal.current_review_status == ReviewStatus(current_review_status)
            )
        query = query.order_by(SemanticProposal.created_at, SemanticProposal.semantic_id)
        return list(self.session.scalars(query.offset(offset).limit(min(limit, 1000))))

    def list_by_version(self, version_id: uuid.UUID, **filters) -> list[SemanticProposal]:
        return self.list(knowledge_version_id=version_id, **filters)

    def list_by_status(self, status: ReviewStatus | str, **filters) -> list[SemanticProposal]:
        return self.list(current_review_status=status, **filters)

    def list_by_screen(self, screen_id: uuid.UUID, **filters) -> list[SemanticProposal]:
        return self.list(screen_knowledge_item_id=screen_id, **filters)

    def list_pending(self, **filters) -> list[SemanticProposal]:
        return self.list_by_status(ReviewStatus.PENDING_REVIEW, **filters)
