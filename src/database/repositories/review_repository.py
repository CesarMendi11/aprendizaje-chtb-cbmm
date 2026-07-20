import uuid

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

    def latest_correction(self, item_id: uuid.UUID | str) -> ReviewAction | None:
        for action in reversed(self.history(item_id)):
            if str(action.action) == "reset_to_pending":
                return None
            if action.corrected_payload is not None:
                return action
        return None
