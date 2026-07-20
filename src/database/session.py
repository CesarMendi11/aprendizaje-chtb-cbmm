from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config.database_settings import DatabaseSettings


def create_engine_from_settings(
    settings: DatabaseSettings, *, require_postgresql: bool = True
) -> Engine:
    url = settings.require_url(postgresql=require_postgresql)
    kwargs = {"echo": settings.echo, "pool_pre_ping": True}
    if not url.startswith("sqlite"):
        kwargs.update(pool_size=settings.pool_size, max_overflow=settings.max_overflow)
    return create_engine(url, **kwargs)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        with session.begin():
            yield session
