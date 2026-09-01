from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from playwright.sync_api import Page, Response

from erp_assistant.structural.canonical.privacy import contains_sensitive

_SAFE_QUERY_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
_LONG_HEX = re.compile(r"^[0-9a-f]{16,}$", re.I)
_LONG_NUMBER = re.compile(r"^\d{6,}$")
_FORBIDDEN_QUERY_KEYS = {
    "authorization",
    "cookie",
    "email",
    "jwt",
    "password",
    "passwd",
    "secret",
    "session",
    "token",
    "username",
}
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class NetworkObservation:
    screen_route: str
    method: str
    endpoint_path: str
    origin_id: str
    origin_kind: str
    resource_type: str
    query_keys: tuple[str, ...] = ()
    status_codes: tuple[int, ...] = ()
    observed_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "screen_route": self.screen_route,
            "method": self.method,
            "endpoint_path": self.endpoint_path,
            "origin_id": self.origin_id,
            "origin_kind": self.origin_kind,
            "resource_type": self.resource_type,
            "query_keys": list(self.query_keys),
            "status_codes": list(self.status_codes),
            "observed_count": self.observed_count,
        }


@dataclass
class _Aggregate:
    count: int = 0
    status_codes: set[int] = field(default_factory=set)


class NetworkEvidenceCollector:
    """Collect sanitized XHR/fetch metadata without request/response bodies or headers."""

    def __init__(self, page: Page, profile: dict[str, Any]):
        self.page = page
        config = profile.get("network_evidence", {}) or {}
        self.enabled = bool(config.get("enabled", True))
        self.include_query_keys = bool(config.get("include_query_keys", True))
        self.max_records = max(1, int(config.get("max_records", 2_000)))
        resource_types = config.get("resource_types", ["xhr", "fetch"])
        self.resource_types = {
            str(value).strip().casefold() for value in resource_types if str(value).strip()
        }
        base = urlsplit(str((profile.get("erp") or {}).get("base_url") or ""))
        self._base_origin = self._origin_tuple(base)
        self._records: dict[tuple[Any, ...], _Aggregate] = {}
        self._dropped = Counter()
        if self.enabled:
            page.on("response", self._on_response)

    @property
    def observation_count(self) -> int:
        return sum(item.count for item in self._records.values())

    @property
    def unique_observation_count(self) -> int:
        return len(self._records)

    def to_dict(self) -> dict[str, Any]:
        observations = [
            NetworkObservation(
                screen_route=key[0],
                method=key[1],
                endpoint_path=key[2],
                origin_id=key[3],
                origin_kind=key[4],
                resource_type=key[5],
                query_keys=key[6],
                status_codes=tuple(sorted(value.status_codes)),
                observed_count=value.count,
            ).to_dict()
            for key, value in sorted(self._records.items(), key=lambda pair: pair[0])
        ]
        return {
            "schema_version": "1.0.0",
            "capture_policy": {
                "bodies_captured": False,
                "headers_captured": False,
                "query_values_captured": False,
                "resource_types": sorted(self.resource_types),
            },
            "observations": observations,
            "statistics": {
                "unique_observations": len(observations),
                "total_observations": self.observation_count,
                "dropped": dict(sorted(self._dropped.items())),
            },
        }

    def _on_response(self, response: Response) -> None:
        try:
            self._capture(response)
        except Exception:
            # Network evidence is observational. A browser callback must never
            # alter crawler execution or policy decisions.
            self._dropped["collector_error"] += 1

    def _capture(self, response: Response) -> None:
        request = response.request
        resource_type = str(request.resource_type or "").casefold()
        if resource_type not in self.resource_types:
            return

        parsed = urlsplit(str(response.url or ""))
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            self._dropped["unsupported_url"] += 1
            return

        screen_route = self._screen_route(self.page.url)
        endpoint_path = self._sanitize_path(parsed.path)
        if not screen_route or not endpoint_path:
            self._dropped["invalid_route_or_path"] += 1
            return

        method = str(request.method or "GET").upper()
        if method not in _ALLOWED_METHODS:
            method = "OTHER"

        origin_tuple = self._origin_tuple(parsed)
        if origin_tuple == self._base_origin:
            origin_kind = "same_origin"
            origin_id = "same_origin"
        else:
            origin_kind = "external"
            origin_id = (
                "external:"
                + hashlib.sha256("|".join(origin_tuple).encode("utf-8")).hexdigest()[:16]
            )

        query_keys = self._query_keys(parsed.query) if self.include_query_keys else ()
        key = (
            screen_route,
            method,
            endpoint_path,
            origin_id,
            origin_kind,
            resource_type,
            query_keys,
        )
        aggregate = self._records.get(key)
        if aggregate is None:
            if len(self._records) >= self.max_records:
                self._dropped["max_records"] += 1
                return
            aggregate = _Aggregate()
            self._records[key] = aggregate
        aggregate.count += 1
        status = int(response.status or 0)
        if 100 <= status <= 599:
            aggregate.status_codes.add(status)

    @staticmethod
    def _origin_tuple(parsed) -> tuple[str, str, str]:
        scheme = str(parsed.scheme or "").casefold()
        host = str(parsed.hostname or "").casefold()
        port_value = parsed.port
        if (scheme == "https" and port_value in {None, 443}) or (
            scheme == "http" and port_value in {None, 80}
        ):
            port = ""
        else:
            port = str(port_value or "")
        return scheme, host, port

    @classmethod
    def _screen_route(cls, url: str) -> str:
        parsed = urlsplit(str(url or ""))
        return cls._normalize_route(parsed.path)

    @staticmethod
    def _normalize_route(path: str) -> str:
        value = str(path or "").strip()
        if not value.startswith("/"):
            return ""
        if len(value) > 1:
            value = value.rstrip("/")
        return value or "/"

    @classmethod
    def _sanitize_path(cls, raw_path: str) -> str:
        path = cls._normalize_route(unquote(str(raw_path or "")))
        if not path:
            return ""
        sanitized: list[str] = []
        for raw_segment in path.split("/"):
            if not raw_segment:
                continue
            segment = raw_segment.strip()
            if cls._dynamic_segment(segment):
                sanitized.append("{id}")
                continue
            if contains_sensitive(segment) or len(segment) > 96:
                sanitized.append("{value}")
                continue
            sanitized.append(segment)
        return "/" + "/".join(sanitized) if sanitized else "/"

    @staticmethod
    def _dynamic_segment(segment: str) -> bool:
        return bool(
            _UUID.fullmatch(segment)
            or _LONG_HEX.fullmatch(segment)
            or _LONG_NUMBER.fullmatch(segment)
        )

    @staticmethod
    def _query_keys(query: str) -> tuple[str, ...]:
        result: set[str] = set()
        for chunk in str(query or "").split("&"):
            key = unquote(chunk.split("=", 1)[0]).strip()
            if not key or not _SAFE_QUERY_KEY.fullmatch(key):
                continue
            lowered = key.casefold()
            if any(term in lowered for term in _FORBIDDEN_QUERY_KEYS):
                continue
            result.add(key)
        return tuple(sorted(result))
