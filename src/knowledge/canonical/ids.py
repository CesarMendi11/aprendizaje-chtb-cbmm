from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def normalize_route(value: Any) -> str:
    route = str(value or "/").split("?", 1)[0].split("#", 1)[0].strip()
    if not route.startswith("/"):
        route = "/" + route
    return route.rstrip("/") or "/"


def stable_id(entity_type: str, *parts: Any, length: int = 24) -> str:
    canonical = json.dumps(
        [normalize_text(entity_type), *[_stable_part(part) for part in parts]],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]
    return f"{normalize_text(entity_type).replace(' ', '_')}:{digest}"


def content_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_part(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.strip().casefold().split())
    if isinstance(value, dict):
        return {str(key): _stable_part(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_stable_part(item) for item in value]
    return value
