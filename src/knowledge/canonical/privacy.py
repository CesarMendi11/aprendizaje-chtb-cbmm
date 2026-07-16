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
    return any(pattern.search(text) for pattern in PATTERNS)


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
