from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
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
    return sorted(normalized)


def semantic_evidence_hash(evidence_payload: dict[str, Any], evidence_ids: list[str]) -> str:
    """Preserve the historical Phase 2 identity for ordinary evidence dictionaries."""
    return canonical_json_hash(
        {"evidence_payload": evidence_payload, "evidence_ids": evidence_ids}
    )


@dataclass(frozen=True, init=False)
class ValidatedSemanticEvidenceSnapshot:
    """Immutable, canonical JSON produced only from a validated screen evidence package."""

    _canonical_json: str
    _evidence_hash: str
    _evidence_ids: tuple[str, ...]

    def __new__(cls, *args, **kwargs):
        raise TypeError(
            "ValidatedSemanticEvidenceSnapshot solo puede construirse mediante su fábrica"
        )

    @property
    def payload(self) -> dict[str, Any]:
        payload, _, _ = semantic_evidence_snapshot_values(self)
        return payload

    @property
    def evidence_hash(self) -> str:
        _, evidence_hash, _ = semantic_evidence_snapshot_values(self)
        return evidence_hash

    @property
    def evidence_ids(self) -> list[str]:
        _, _, evidence_ids = semantic_evidence_snapshot_values(self)
        return evidence_ids


def semantic_evidence_snapshot_values(
    snapshot: ValidatedSemanticEvidenceSnapshot,
) -> tuple[dict[str, Any], str, list[str]]:
    """Return validated detached values, sanitizing malformed snapshot instances."""
    if not isinstance(snapshot, ValidatedSemanticEvidenceSnapshot):
        raise SemanticPayloadError("El snapshot de evidencia no es válido")
    try:
        canonical_json = object.__getattribute__(snapshot, "_canonical_json")
        evidence_hash = object.__getattribute__(snapshot, "_evidence_hash")
        evidence_ids = object.__getattribute__(snapshot, "_evidence_ids")
        payload = json.loads(canonical_json)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SemanticPayloadError("El snapshot de evidencia no es válido") from exc
    if not isinstance(payload, dict) or not payload:
        raise SemanticPayloadError("El snapshot de evidencia no es válido")
    digest = validate_sha256(evidence_hash, field="evidence_hash")
    if not isinstance(evidence_ids, tuple):
        raise SemanticPayloadError("El snapshot de evidencia no es válido")
    normalized_ids = normalize_evidence_ids(list(evidence_ids))
    embedded_ids = normalize_evidence_ids(payload.get("evidence_ids"))
    if embedded_ids != normalized_ids or canonical_json_hash(payload) != digest:
        raise SemanticPayloadError("El snapshot de evidencia no es válido")
    return copy.deepcopy(payload), digest, list(normalized_ids)


def validated_semantic_evidence_snapshot(package: Any) -> ValidatedSemanticEvidenceSnapshot:
    from src.analysis.schemas import ScreenEvidencePackage

    if not isinstance(package, ScreenEvidencePackage):
        raise SemanticPayloadError("Se requiere un ScreenEvidencePackage validado")
    validated = ScreenEvidencePackage.model_validate(package.model_dump(mode="python"))
    value = validated.model_dump(mode="json", exclude={"evidence_hash"})
    if not isinstance(value, dict) or not value:
        raise SemanticPayloadError("evidence_payload no puede estar vacío")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise SemanticPayloadError("evidence_payload no es JSON serializable") from exc
    if len(encoded) > MAX_CORRECTION_BYTES:
        raise SemanticPayloadError("evidence_payload excede el tamaño permitido")
    forbidden = {"password", "passwd", "token", "cookie", "authorization", "html"}
    for key, item in _walk_json(value):
        lowered_key = key.casefold()
        if lowered_key in forbidden:
            raise SemanticSensitiveContentError("evidence_payload contiene una clave prohibida")
        if isinstance(item, str) and (
            "<script" in item.casefold() or "javascript:" in item.casefold()
        ):
            raise SemanticSensitiveContentError("evidence_payload contiene texto ejecutable")
    normalized_ids = normalize_evidence_ids(list(validated.evidence_ids))
    if normalized_ids != list(validated.evidence_ids):
        raise SemanticPayloadError("evidence_ids no coincide con el paquete validado")
    evidence_hash = validate_sha256(validated.evidence_hash, field="evidence_hash")
    if canonical_json_hash(value) != evidence_hash:
        raise SemanticPayloadError("evidence_hash no coincide con el paquete validado")
    snapshot = object.__new__(ValidatedSemanticEvidenceSnapshot)
    object.__setattr__(snapshot, "_canonical_json", encoded.decode("utf-8"))
    object.__setattr__(snapshot, "_evidence_hash", evidence_hash)
    object.__setattr__(snapshot, "_evidence_ids", tuple(normalized_ids))
    return snapshot


def _walk_json(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)


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
