from __future__ import annotations

import copy
import json
from typing import Any

from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.privacy import contains_sensitive

VOLATILE_KEYS = {
    "generated_at",
    "reviewed_at",
    "reviewed_by",
    "review_notes",
    "created_at",
    "updated_at",
    "imported_at",
}
OPERATIONAL_KEYS = VOLATILE_KEYS | {"review_status", "review_revision"}
MAX_CORRECTION_BYTES = 256_000


def review_action_payload(action: Any) -> dict[str, Any]:
    """Return the stable, non-sensitive public representation of an action."""
    created_at = action.created_at
    return {
        "id": str(action.id),
        "action": str(action.action),
        "previous_status": str(action.previous_status),
        "new_status": str(action.new_status),
        "source": str(action.source),
        "created_at": created_at.isoformat() if created_at else None,
    }


def functional_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): clean(item)
                for key, item in sorted(value.items())
                if str(key).casefold() not in VOLATILE_KEYS
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(payload)


def item_content_hash(payload: dict[str, Any]) -> str:
    return content_hash(functional_payload(payload))


def validate_safe_json(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("La corrección debe ser un objeto JSON")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > MAX_CORRECTION_BYTES:
        raise ValueError("La corrección excede el tamaño permitido")
    for key, value in _walk(payload):
        lowered = key.casefold()
        if lowered in OPERATIONAL_KEYS:
            raise ValueError(f"Clave operativa no permitida: {key}")
        if lowered in {
            "password",
            "passwd",
            "token",
            "cookie",
            "authorization",
            "email",
            "ip",
            "html",
            "screenshot",
        }:
            raise ValueError(f"Contenido sensible no permitido: {key}")
        if isinstance(value, str) and (
            contains_sensitive(value)
            or "<script" in value.casefold()
            or "javascript:" in value.casefold()
        ):
            raise ValueError("La corrección contiene datos sensibles o HTML ejecutable")
    return copy.deepcopy(payload)


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
