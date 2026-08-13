from __future__ import annotations

import re
from typing import Any

SENSITIVE_REGIONS = {"volatile", "header", "session", "user", "authentication"}

CANONICAL_ID = re.compile(
    r"^(?:erp|module|screen|ui_state|field|control|table|table_column|link|event|transition|evidence|semantic)(?::[A-Za-z0-9._-]+)+$",
    re.I,
)
PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"(?<![\w:])(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]{1,39}(?![\w:])", re.I),
    re.compile(r"\b(?:bearer\s+)?[A-Za-z0-9_-]{32,}\b", re.I),
    re.compile(r"\b(?:token|password|passwd|secret|cookie|session)\s*[:=]\s*\S+", re.I),
    # Concrete business values. Labels such as "RUC", "Factura", "Monto" and
    # "Fecha" intentionally do not match without an accompanying value.
    re.compile(r"\b\d{3}-\d{3}-\d{9}\b"),
    # Concrete numeric identifiers must stand on their own. Numeric runs
    # embedded inside canonical/alphanumeric IDs such as
    # ``screen:8835443310af`` are not business values.
    re.compile(r"(?<![\w.-])\d{13}(?![\w.-])"),
    re.compile(r"(?<![\w.-])\d{10}(?![\w.-])"),
    re.compile(r"(?<![\w.-])\d{7,}(?![\w.-])"),
    re.compile(r"(?<!\w)(?:USD\s*)?[$€£]\s*\d[\d.,]*(?!\w)", re.I),
    re.compile(r"(?<![\w.])\d{1,3}(?:[.,]\d{3})*[.,]\d{2}(?![\w.])"),
    re.compile(r"\b\d{1,2}\s+(?:ene|feb|mar|abr|may|jun|jul|ago|sep|sept|oct|nov|dic)(?:\.|iembre|ubre|osto|io|ayo|il|zo|ero)?\s+\d{4}\b", re.I),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    re.compile(r"(?<!\w)\+\d(?:[\s().-]*\d){7,14}(?!\w)"),
    re.compile(r"(?<![\w-])(?:\d[\s.-]*){7,10}(?![\w-])"),
)
STRUCTURAL_CSS_SEGMENT = r"[A-Za-z][A-Za-z0-9-]{0,127}(?::(?:nth|nth-of-type)\([1-9]\d{0,3}\))?"
STRUCTURAL_CSS_PATH = re.compile(
    rf"^{STRUCTURAL_CSS_SEGMENT}(?:\s*>\s*{STRUCTURAL_CSS_SEGMENT})*$"
)
NAVIGATION_SELECTOR_MAX_LENGTH = 2_000
NAVIGATION_PATH_MAX_LENGTH = 32_000
NAVIGATION_PATH_MAX_DEPTH = 64

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
    # Canonical IDs are structural identifiers, not observed business values.
    # Their hash suffix can be purely numeric by chance, so numeric PII rules
    # must not classify a valid namespaced canonical ID as sensitive.
    if CANONICAL_ID.fullmatch(text):
        return False
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
    result: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        lowered = name.casefold()
        if lowered in forbidden:
            continue
        if lowered in {"navigation_origin", "navigation_origin_path"}:
            if is_safe_navigation_metadata(name, item):
                result[name] = item
            continue
        if _safe_scalar(item):
            result[name] = item
    return result


def is_safe_navigation_metadata(key: Any, value: Any) -> bool:
    name = str(key or "").casefold()
    if name == "navigation_origin":
        return _safe_navigation_selector(value)
    if name == "navigation_origin_path":
        if not isinstance(value, str):
            return False
        text = value.strip()
        if not text or len(text) > NAVIGATION_PATH_MAX_LENGTH:
            return False
        parts = [part.strip() for part in text.split("||")]
        if not parts or len(parts) > NAVIGATION_PATH_MAX_DEPTH or any(not part for part in parts):
            return False
        return all(_safe_navigation_selector(part) for part in parts)
    return False


def _safe_navigation_selector(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or len(text) > NAVIGATION_SELECTOR_MAX_LENGTH:
        return False
    if not contains_sensitive(text):
        return True
    # ScreenExtractor.cssPath emits tag-only structural paths with child
    # combinators and optional nth/nth-of-type ordinals. Those long custom
    # element names can resemble opaque tokens to the generic privacy filter,
    # but the selector grammar itself cannot carry arbitrary values. Requiring
    # a structural marker prevents a plain long token from being whitelisted.
    has_structural_marker = (
        ">" in text or ":nth(" in text or ":nth-of-type(" in text
    )
    if not has_structural_marker or not STRUCTURAL_CSS_PATH.fullmatch(text):
        return False
    # Standard HTML tags are short; long generated tag names are expected to
    # be custom elements (and therefore hyphenated). This keeps an opaque long
    # token embedded after a child combinator from masquerading as a selector.
    for segment in re.split(r"\s*>\s*", text):
        tag = segment.split(":", 1)[0]
        if len(tag) >= 32 and "-" not in tag:
            return False
    return True


def _safe_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and not contains_sensitive(value) and len(value) <= 500
