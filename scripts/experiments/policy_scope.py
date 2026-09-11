from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from erp_assistant.structural.canonical.ids import normalize_route


@dataclass(frozen=True)
class PolicyScope:
    contract_id: str
    blocked_route_prefix: str
    policy_blocked_routes: frozenset[str]
    source_path: Path
    file_sha256: str

    def is_reference_blocked(self, route: str) -> bool:
        return normalize_route(route) in self.policy_blocked_routes

    def is_under_blocked_prefix(self, route: str) -> bool:
        normalized = normalize_route(route)
        return normalized == self.blocked_route_prefix or normalized.startswith(
            f"{self.blocked_route_prefix}/"
        )


def load_policy_scope(path: str | Path) -> PolicyScope:
    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))

    contract_id = str(payload.get("contract_id") or "").strip()
    if not contract_id:
        raise ValueError("policy scope contract_id is required")

    status = str(payload.get("status") or "").strip()
    if not status.startswith("FROZEN"):
        raise ValueError("policy scope contract must be frozen")

    policy_source = payload.get("policy_source")
    if not isinstance(policy_source, dict):
        raise ValueError("policy scope policy_source must be an object")

    prefix_raw = str(policy_source.get("blocked_route_prefix") or "").strip()
    if not prefix_raw:
        raise ValueError("policy scope blocked_route_prefix is required")
    blocked_route_prefix = normalize_route(prefix_raw)

    routes_raw = payload.get("policy_blocked_routes")
    if not isinstance(routes_raw, list) or not routes_raw:
        raise ValueError("policy scope policy_blocked_routes must be a non-empty list")

    route_values = [
        str(route or "").strip()
        for route in routes_raw
    ]
    if any(not route for route in route_values):
        raise ValueError("policy scope contains a blank blocked route")
    normalized_routes = [
        normalize_route(route)
        for route in route_values
    ]
    if len(set(normalized_routes)) != len(normalized_routes):
        raise ValueError("policy scope contains duplicate blocked routes")

    for route in normalized_routes:
        if not (
            route == blocked_route_prefix
            or route.startswith(f"{blocked_route_prefix}/")
        ):
            raise ValueError(
                "policy blocked route is outside blocked_route_prefix: "
                f"{route}"
            )

    return PolicyScope(
        contract_id=contract_id,
        blocked_route_prefix=blocked_route_prefix,
        policy_blocked_routes=frozenset(normalized_routes),
        source_path=source_path,
        file_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
    )
