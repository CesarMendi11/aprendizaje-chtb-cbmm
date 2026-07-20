from __future__ import annotations

import re
from typing import Any

SENSITIVE_REGIONS = {"volatile", "header", "session", "user", "authentication"}
PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]{1,39}(?![\w:])", re.I),
    re.compile(r"\b(?:bearer\s+)?[A-Za-z0-9_-]{32,}\b", re.I),
    re.compile(r"\b(?:token|password|passwd|secret|cookie|session)\s*[:=]\s*\S+", re.I),
    # Concrete business values. Labels such as "RUC", "Factura", "Monto" and
    # "Fecha" intentionally do not match without an accompanying value.
    re.compile(r"\b\d{3}-\d{3}-\d{9}\b"),
    re.compile(r"(?<!\d)\d{13}(?!\d)"),
    re.compile(r"(?<!\d)\d{10}(?!\d)"),
    re.compile(r"(?<![\w.-])\d{7,}(?![\w.-])"),
    re.compile(r"(?<!\w)(?:USD\s*)?[$€£]\s*\d[\d.,]*(?!\w)", re.I),
    re.compile(r"(?<![\w.])\d{1,3}(?:[.,]\d{3})*[.,]\d{2}(?![\w.])"),
    re.compile(r"\b\d{1,2}\s+(?:ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic)(?:\.|iembre|ubre|osto|io|ayo|il|zo|ero)?\s+\d{4}\b", re.I),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"(?<!\w)\+\d(?:[\s().-]*\d){7,14}(?!\w)"),
    re.compile(r"(?<![\w-])(?:\d[\s.-]*){7,10}(?![\w-])"),
)
VOLATILE = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"historial de inicio de sesi[oó]n.*", re.I),
)


def sanitize_text(value: Any, limit: int = 4000) -> tuple[str, int]:
    text = " ".join(str(value or "").split())
    detections = 0
    for pattern in (*PATTERNS, *VOLATILE):
        text, count = pattern.subn(" ", text)
        detections += count
    return " ".join(text.split())[:limit], detections


def contains_sensitive(value: Any) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in (*PATTERNS, *VOLATILE))


def build_safe_structural_text(
    title: Any,
    fragments: Any,
    *,
    limit: int = 2000,
) -> tuple[str, int]:
    """Build screen prose solely from already extracted structural labels."""
    result: list[str] = []
    seen: set[str] = set()
    exclusions = 0
    for value in (title, *fragments):
        raw = " ".join(str(value or "").split())
        if not raw:
            continue
        clean, detections = sanitize_text(raw, limit)
        # Dropping the complete fragment avoids retaining a partial business value.
        if detections or not clean:
            exclusions += max(1, detections)
            continue
        key = clean.casefold()
        if key in seen:
            continue
        candidate = " | ".join((*result, clean))
        if len(candidate) > limit:
            exclusions += 1
            continue
        seen.add(key)
        result.append(clean)
    return " | ".join(result), exclusions


def safe_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    forbidden = {"password", "token", "cookie", "authorization", "username", "email", "session"}
    return {
        str(key): item
        for key, item in value.items()
        if str(key).casefold() not in forbidden and _safe_scalar(item)
    }


def _safe_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and not contains_sensitive(value) and len(value) <= 500
