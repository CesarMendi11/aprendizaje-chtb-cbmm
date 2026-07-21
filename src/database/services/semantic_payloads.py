from __future__ import annotations

import copy
import json
import re
from typing import Any

from src.database.services.payloads import MAX_CORRECTION_BYTES, validate_safe_json
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.privacy import sanitize_text

from .semantic_exceptions import (
    SemanticPayloadError,
    SemanticSensitiveContentError,
)

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_PURPOSE_SUMMARY_CHARS = 2000


def canonical_json_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SemanticPayloadError("El contenido no es JSON serializable") from exc
    return content_hash(json.loads(encoded.decode("utf-8")))


def validate_sha256(value: Any, *, field: str) -> str:
    candidate = str(value or "").strip().casefold()
    if not SHA256_PATTERN.fullmatch(candidate):
        raise SemanticPayloadError(f"{field} debe ser un SHA-256 hexadecimal")
    return candidate


def validate_semantic_payload(
    value: Any, *, field: str, require_purpose_summary: bool = False, allow_empty: bool = False
) -> dict[str, Any]:
    try:
        payload = validate_safe_json(value)
    except ValueError as exc:
        message = str(exc)
        error = (
            SemanticSensitiveContentError(message)
            if "sensible" in message or "no permitido" in message
            else SemanticPayloadError(message)
        )
        raise error from exc
    if not payload and not allow_empty:
        raise SemanticPayloadError(f"{field} no puede estar vacío")
    try:
        size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SemanticPayloadError(f"{field} no es JSON serializable") from exc
    if size > MAX_CORRECTION_BYTES:
        raise SemanticPayloadError(f"{field} excede el tamaño permitido")
    if require_purpose_summary:
        summary = payload.get("purpose_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise SemanticPayloadError("purpose_summary es obligatorio y debe ser texto")
        clean, detections = sanitize_text(summary, MAX_PURPOSE_SUMMARY_CHARS + 1)
        if detections:
            raise SemanticSensitiveContentError("purpose_summary contiene datos sensibles")
        if not clean or len(clean) > MAX_PURPOSE_SUMMARY_CHARS:
            raise SemanticPayloadError("purpose_summary excede el tamaño permitido")
        payload["purpose_summary"] = clean
    return copy.deepcopy(payload)


def normalize_evidence_ids(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise SemanticPayloadError("evidence_ids debe ser una lista")
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise SemanticPayloadError("Cada evidence_id debe ser texto no vacío")
        clean, detections = sanitize_text(value, 241)
        if detections:
            raise SemanticSensitiveContentError("evidence_ids contiene datos sensibles")
        if not clean or len(clean) > 240:
            raise SemanticPayloadError("evidence_id inválido o demasiado largo")
        normalized.add(clean)
    if not normalized:
        raise SemanticPayloadError("evidence_ids no puede estar vacío")
    return sorted(normalized)


def semantic_review_action_payload(action: Any) -> dict[str, Any]:
    return {
        "id": str(action.id),
        "action": str(action.action),
        "previous_status": str(action.previous_status),
        "new_status": str(action.new_status),
        "corrected_payload": copy.deepcopy(action.corrected_payload),
        "review_notes": action.review_notes,
        "reviewer_subject": action.reviewer_subject,
        "source": action.source,
        "proposal_content_hash": action.proposal_content_hash,
        "created_at": action.created_at.isoformat() if action.created_at else None,
    }
