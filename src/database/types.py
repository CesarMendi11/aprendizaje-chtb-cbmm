from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

JSONType = JSON().with_variant(JSONB(), "postgresql")


class StringEnum(TypeDecorator):
    impl = String(40)
    cache_ok = True

    def __init__(self, enum_class: type[enum.StrEnum], *args: Any, **kwargs: Any):
        self.enum_class = enum_class
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        return None if value is None else self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Any) -> enum.StrEnum | None:
        return None if value is None else self.enum_class(value)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

