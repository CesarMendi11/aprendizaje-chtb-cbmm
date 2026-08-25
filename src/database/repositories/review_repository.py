import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import ReviewAction


class ReviewRepository:
    def __init__(self, session: Session):
        self.session = session

    def history(self, item_id: uuid.UUID | str) -> list[ReviewAction]:
        return list(
            self.session.scalars(
                select(ReviewAction)
                .where(ReviewAction.knowledge_item_id == uuid.UUID(str(item_id)))
                .order_by(ReviewAction.created_at, ReviewAction.id)
            )
        )

    def history_many(
        self, item_ids: list[uuid.UUID | str] | tuple[uuid.UUID | str, ...]
    ) -> dict[uuid.UUID, list[ReviewAction]]:
        identifiers = [uuid.UUID(str(value)) for value in item_ids]
        if not identifiers:
            return {}
        grouped: dict[uuid.UUID, list[ReviewAction]] = defaultdict(list)
        for action in self.session.scalars(
            select(ReviewAction)
            .where(ReviewAction.knowledge_item_id.in_(identifiers))
            .order_by(
                ReviewAction.knowledge_item_id,
                ReviewAction.created_at,
                ReviewAction.id,
            )
        ):
            grouped[action.knowledge_item_id].append(action)
        return dict(grouped)

    @staticmethod
    def latest_correction_from_history(
        actions: list[ReviewAction] | tuple[ReviewAction, ...],
    ) -> ReviewAction | None:
        for action in reversed(actions):
            if str(action.action) == "reset_to_pending":
                return None
            if action.corrected_payload is not None:
                return action
        return None

    def latest_correction(self, item_id: uuid.UUID | str) -> ReviewAction | None:
        return self.latest_correction_from_history(self.history(item_id))
