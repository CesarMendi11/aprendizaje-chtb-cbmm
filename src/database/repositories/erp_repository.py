from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import ERPSystemRecord


class ERPRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, erp_id: str) -> ERPSystemRecord | None:
        return self.session.get(ERPSystemRecord, erp_id)

    def get_by_slug(self, slug: str) -> ERPSystemRecord | None:
        return self.session.scalar(select(ERPSystemRecord).where(ERPSystemRecord.slug == slug))

    def list(self) -> list[ERPSystemRecord]:
        return list(self.session.scalars(select(ERPSystemRecord).order_by(ERPSystemRecord.slug)))

