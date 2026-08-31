from __future__ import annotations

from urllib.parse import urlsplit

from erp_assistant.semantic.schemas.screen_evidence import NetworkTraceEvidence
from erp_assistant.structural.canonical.privacy import sanitize_text

READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ALLOWED_METHODS = READ_ONLY_METHODS | frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALLOWED_ORIGIN_KINDS = frozenset({"same_origin", "external"})
CAPTURE_FLAGS = ("headers_captured", "bodies_captured", "query_values_captured")
MAX_NETWORK_ENDPOINTS = 32
MAX_NETWORK_QUERY_KEYS = 32
MAX_NETWORK_RESOURCE_TYPES = 16
MAX_NETWORK_ORIGIN_KINDS = 4
MAX_NETWORK_STATUS_CODES = 16


def safe_network_trace(evidence_id: str, payload: dict) -> NetworkTraceEvidence | None:
    """Project canonical NETWORK_TRACE metadata into a bounded semantic-safe DTO.

    This function deliberately accepts only the current canonical v1 metadata
    contract. Missing safety provenance or malformed aggregate fields fail closed.
    """
    if str(payload.get("evidence_type") or "") != "network_trace":
        return None
    if str(payload.get("source_entity_type") or "") != "screen":
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if any(metadata.get(key) is not False for key in CAPTURE_FLAGS):
        return None

    methods = _safe_methods(metadata.get("methods"))
    endpoints = _safe_endpoints(metadata.get("endpoint_paths"))
    resource_types = _safe_tokens(
        metadata.get("resource_types"),
        max_source_chars=200,
        max_items=MAX_NETWORK_RESOURCE_TYPES,
    )
    origin_kinds = _safe_origin_kinds(metadata.get("origin_kinds"))
    query_keys = _safe_tokens(
        metadata.get("query_keys"),
        max_source_chars=500,
        max_items=MAX_NETWORK_QUERY_KEYS,
    )
    status_codes = _safe_status_codes(metadata.get("status_codes"))
    observation_count = _positive_int(metadata.get("observation_count"))
    endpoint_count = _positive_int(metadata.get("endpoint_count"))

    if (
        methods is None
        or endpoints is None
        or resource_types is None
        or origin_kinds is None
        or query_keys is None
        or status_codes is None
        or observation_count is None
        or endpoint_count is None
        or not methods
        or not endpoints
        or not resource_types
        or not origin_kinds
        or endpoint_count < len(endpoints)
    ):
        return None

    return NetworkTraceEvidence(
        evidence_id=evidence_id,
        methods=methods,
        endpoint_paths=endpoints,
        resource_types=resource_types,
        origin_kinds=origin_kinds,
        status_codes=status_codes,
        query_keys=query_keys,
        observation_count=observation_count,
        endpoint_count=endpoint_count,
        read_only=set(methods).issubset(READ_ONLY_METHODS),
    )


def _split_current_metadata(value, *, separator: str, max_chars: int) -> tuple[str, ...] | None:
    if not isinstance(value, str) or len(value) > max_chars:
        return None
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(separator) if part.strip())


def _safe_methods(value) -> tuple[str, ...] | None:
    raw = _split_current_metadata(value, separator=",", max_chars=200)
    if raw is None:
        return None
    methods = tuple(item.upper() for item in raw)
    if any(method not in ALLOWED_METHODS for method in methods):
        return None
    return tuple(sorted(set(methods)))


def _safe_endpoints(value) -> tuple[str, ...] | None:
    raw = _split_current_metadata(value, separator=" | ", max_chars=2000)
    if raw is None:
        return None
    if any(not _safe_endpoint(item) for item in raw):
        return None
    return tuple(sorted(set(raw)))[:MAX_NETWORK_ENDPOINTS]


def _safe_origin_kinds(value) -> tuple[str, ...] | None:
    raw = _split_current_metadata(value, separator=",", max_chars=200)
    if raw is None:
        return None
    normalized = tuple(item.casefold() for item in raw)
    if any(item not in ALLOWED_ORIGIN_KINDS for item in normalized):
        return None
    return tuple(sorted(set(normalized)))[:MAX_NETWORK_ORIGIN_KINDS]


def _safe_tokens(value, *, max_source_chars: int, max_items: int) -> tuple[str, ...] | None:
    raw = _split_current_metadata(value, separator=",", max_chars=max_source_chars)
    if raw is None:
        return None
    if any(not _safe_metadata_token(item) for item in raw):
        return None
    return tuple(sorted(set(raw)))[:max_items]


def _safe_status_codes(value) -> tuple[int, ...] | None:
    raw = _split_current_metadata(value, separator=",", max_chars=200)
    if raw is None:
        return None
    result: list[int] = []
    for item in raw:
        try:
            status = int(item)
        except (TypeError, ValueError):
            return None
        if not 100 <= status <= 599:
            return None
        result.append(status)
    return tuple(sorted(set(result)))[:MAX_NETWORK_STATUS_CODES]


def _safe_metadata_token(value: str) -> bool:
    if not value or len(value) > 80:
        return False
    clean, detections = sanitize_text(value, 81)
    return bool(clean and not detections and clean == value)


def _safe_endpoint(value: str) -> bool:
    if not value or len(value) > 500:
        return False
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return False
    if not parsed.path.startswith("/"):
        return False
    clean, detections = sanitize_text(value, 501)
    return bool(clean and not detections and clean == value)


def _positive_int(value) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return None
    return value
