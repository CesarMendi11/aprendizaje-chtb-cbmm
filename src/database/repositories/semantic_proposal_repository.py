from __future__ import annotations

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from src.database.models import (
    ERPSystemRecord,
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
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

    def get_detail_by_semantic_id(self, semantic_id: str) -> SemanticProposal | None:
        return self.session.scalar(
            select(SemanticProposal)
            .where(SemanticProposal.semantic_id == semantic_id)
            .options(
                joinedload(SemanticProposal.knowledge_version).joinedload(
                    KnowledgeVersionRecord.erp
                ),
                joinedload(SemanticProposal.screen_knowledge_item),
            )
        )

    def list_admin_page(
        self,
        *,
        current_review_status: ReviewStatus | str | None = None,
        semantic_type: str | None = None,
        erp_id: str | None = None,
        knowledge_version_id: uuid.UUID | None = None,
        screen_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[tuple[SemanticProposal, int]], int]:
        filters = []
        if current_review_status is not None:
            filters.append(
                SemanticProposal.current_review_status == ReviewStatus(current_review_status)
            )
        if semantic_type is not None:
            filters.append(SemanticProposal.semantic_type == semantic_type)
        if erp_id is not None:
            filters.append(ERPSystemRecord.id == erp_id)
        if knowledge_version_id is not None:
            filters.append(SemanticProposal.knowledge_version_id == knowledge_version_id)
        if screen_id is not None:
            filters.append(KnowledgeItem.canonical_id == screen_id)
        base = (
            select(SemanticProposal)
            .join(SemanticProposal.knowledge_version)
            .join(KnowledgeVersionRecord.erp)
            .join(SemanticProposal.screen_knowledge_item)
            .where(*filters)
        )
        total = self.session.scalar(
            select(func.count()).select_from(base.subquery())
        ) or 0
        action_counts = (
            select(
                SemanticReviewAction.semantic_proposal_id.label("proposal_id"),
                func.count(SemanticReviewAction.id).label("action_count"),
            )
            .group_by(SemanticReviewAction.semantic_proposal_id)
            .subquery()
        )
        pending_first = case(
            (SemanticProposal.current_review_status == ReviewStatus.PENDING_REVIEW, 0),
            else_=1,
        )
        query = (
            base.add_columns(func.coalesce(action_counts.c.action_count, 0))
            .outerjoin(action_counts, action_counts.c.proposal_id == SemanticProposal.id)
            .options(
                joinedload(SemanticProposal.knowledge_version).joinedload(
                    KnowledgeVersionRecord.erp
                ),
                joinedload(SemanticProposal.screen_knowledge_item),
            )
            .order_by(pending_first, SemanticProposal.created_at, SemanticProposal.semantic_id)
            .offset(offset)
            .limit(limit)
        )
        return [(row[0], int(row[1])) for row in self.session.execute(query)], int(total)

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
