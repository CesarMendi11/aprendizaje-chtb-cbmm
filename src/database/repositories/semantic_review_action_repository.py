from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import ReviewActionType
from src.database.models import SemanticReviewAction


class SemanticReviewActionRepository:
    def __init__(self, session: Session):
        self.session = session

    def append(self, action: SemanticReviewAction) -> SemanticReviewAction:
        self.session.add(action)
        return action

    def list_for_proposal(self, proposal_id: uuid.UUID | str) -> list[SemanticReviewAction]:
        return list(
            self.session.scalars(
                select(SemanticReviewAction)
                .where(SemanticReviewAction.semantic_proposal_id == uuid.UUID(str(proposal_id)))
                .order_by(SemanticReviewAction.created_at, SemanticReviewAction.id)
            )
        )

    def latest_for_proposal(self, proposal_id: uuid.UUID | str) -> SemanticReviewAction | None:
        return self.session.scalar(
            select(SemanticReviewAction)
            .where(SemanticReviewAction.semantic_proposal_id == uuid.UUID(str(proposal_id)))
            .order_by(SemanticReviewAction.created_at.desc(), SemanticReviewAction.id.desc())
            .limit(1)
        )

    def latest_reset(self, proposal_id: uuid.UUID | str) -> SemanticReviewAction | None:
        return self.session.scalar(
            select(SemanticReviewAction)
            .where(
                SemanticReviewAction.semantic_proposal_id == uuid.UUID(str(proposal_id)),
                SemanticReviewAction.action == ReviewActionType.RESET_TO_PENDING,
            )
            .order_by(SemanticReviewAction.created_at.desc(), SemanticReviewAction.id.desc())
            .limit(1)
        )

    def latest_correction_after_last_reset(
        self, proposal_id: uuid.UUID | str
    ) -> SemanticReviewAction | None:
        for action in reversed(self.list_for_proposal(proposal_id)):
            if action.action == ReviewActionType.RESET_TO_PENDING:
                return None
            if action.action == ReviewActionType.CORRECT and action.corrected_payload is not None:
                return action
        return None
