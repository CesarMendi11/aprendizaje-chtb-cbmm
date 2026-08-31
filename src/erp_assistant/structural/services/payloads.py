from __future__ import annotations

import copy
import json
from typing import Any

from erp_assistant.structural.canonical.ids import content_hash
from erp_assistant.structural.canonical.privacy import contains_sensitive, is_safe_navigation_metadata

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

# Fields that prove where/how an observation was captured, but do not change
# the structural fact a reviewer is deciding on. They remain in source_payload
# for auditability and can still affect raw KnowledgeItem.content_hash.
STRUCTURAL_REVIEW_PROVENANCE_KEYS = {
    "source_refs",
    "evidence_ids",
}
STRUCTURAL_REVIEW_ENTITY_IGNORED_KEYS = {
    "evidence": {"artifact_path", "artifact_hash", "captured_at"},
    "ui_state": {"exact_fingerprint", "observed_path", "restore_path"},
}


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
    """Hash the complete generated source payload, excluding volatile review metadata."""
    return content_hash(functional_payload(payload))


def structural_review_payload(entity_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return the stable structural facts that require human re-review.

    Provenance refreshes stay preserved in ``source_payload`` and in the raw
    item hash, but they must not turn the same structural fact into MODIFIED.
    ``UIState.exact_fingerprint`` and replay paths are observational/operational;
    the structural fingerprint is the governed state identity.
    """
    cleaned = functional_payload(payload)
    for key in STRUCTURAL_REVIEW_PROVENANCE_KEYS:
        cleaned.pop(key, None)
    for key in STRUCTURAL_REVIEW_ENTITY_IGNORED_KEYS.get(str(entity_type), set()):
        cleaned.pop(key, None)
    return cleaned


def structural_review_hash(entity_type: str, payload: dict[str, Any]) -> str:
    return content_hash(structural_review_payload(entity_type, payload))


def rebase_structural_correction(
    entity_type: str,
    corrected_payload: dict[str, Any],
    current_source_payload: dict[str, Any],
) -> dict[str, Any]:
    """Carry a human correction onto refreshed provenance without making it stale.

    The human-authored functional fields are preserved. Provenance/operational
    fields ignored by structural review are always taken from the new generated
    source so corrections never keep old run paths, evidence links or replay data.
    """
    rebased = copy.deepcopy(corrected_payload)
    current = copy.deepcopy(current_source_payload)
    ignored = set(STRUCTURAL_REVIEW_PROVENANCE_KEYS)
    ignored.update(STRUCTURAL_REVIEW_ENTITY_IGNORED_KEYS.get(str(entity_type), set()))
    for key in ignored:
        if key in current:
            rebased[key] = current[key]
        else:
            rebased.pop(key, None)
    return rebased


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
            (contains_sensitive(value) and not is_safe_navigation_metadata(key, value))
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
