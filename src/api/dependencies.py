from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session



def get_semantic_review_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.semantic_review_session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_admin_read_session(request: Request) -> Iterator[Session]:
    """Provide a transaction that is read-only on PostgreSQL and always rolled back."""
    factory = request.app.state.semantic_review_session_factory
    session = factory()
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        session.rollback()
        session.close()
