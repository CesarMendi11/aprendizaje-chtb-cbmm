from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .enums import EvidenceType
from .ids import content_hash, normalize_route, stable_id
from .models import CanonicalKnowledgeBase, Evidence
from .privacy import contains_sensitive


class CanonicalNetworkEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalNetworkEvidenceResult:
    knowledge: CanonicalKnowledgeBase
    observation_count: int
    screen_count: int
    sensitive_exclusions: int
    omitted_observations: int

    def report(self) -> dict[str, int]:
        return {
            "observation_count": self.observation_count,
            "screen_count": self.screen_count,
            "sensitive_exclusions": self.sensitive_exclusions,
            "omitted_observations": self.omitted_observations,
        }


class CanonicalNetworkEvidenceIntegrator:
    """Attach sanitized network evidence to canonical screens.

    The crawler artifact is observational only: no request/response bodies,
    headers, cookies, authorization values or query values are accepted here.
    Network evidence never changes the functional knowledge version.
    """

    def __init__(self, project_root: Path | str = "."):
        self.project_root = Path(project_root).resolve()

    def integrate(
        self,
        knowledge: CanonicalKnowledgeBase,
        artifact_path: Path | str,
    ) -> CanonicalNetworkEvidenceResult:
        path = Path(artifact_path)
        if not path.is_file():
            return CanonicalNetworkEvidenceResult(
                knowledge=knowledge,
                observation_count=0,
                screen_count=0,
                sensitive_exclusions=0,
                omitted_observations=0,
            )

        payload = self._load(path)
        self._validate_capture_policy(payload)

        by_route: dict[str, list[dict[str, Any]]] = {}
        sensitive_exclusions = 0
        omitted = 0
        for raw in payload.get("observations") or []:
            clean = self._clean_observation(raw)
            if clean is None:
                omitted += 1
                if isinstance(raw, dict) and self._looks_sensitive(raw):
                    sensitive_exclusions += 1
                continue
            by_route.setdefault(clean["screen_route"], []).append(clean)

        for values in by_route.values():
            values.sort(key=self._observation_sort_key)

        evidence = list(knowledge.evidence)
        screens = []
        attached_observations = 0
        attached_screens = 0
        relative_artifact = self._relative(path)
        container_hash = hashlib.sha256(path.read_bytes()).hexdigest()

        for screen in knowledge.screens:
            observations = by_route.get(screen.route, [])
            if not observations:
                screens.append(screen)
                continue

            attached_screens += 1
            attached_observations += sum(item["observed_count"] for item in observations)
            evidence_id = stable_id(
                "evidence",
                "screen",
                screen.id,
                "network_evidence.json",
            )
            evidence.append(
                Evidence(
                    id=evidence_id,
                    evidence_type=EvidenceType.NETWORK_TRACE,
                    artifact_path=relative_artifact,
                    artifact_hash=content_hash(observations),
                    source_entity_type="screen",
                    source_entity_id=screen.id,
                    metadata=self._metadata(observations),
                )
            )
            screens.append(
                screen.model_copy(
                    update={
                        "source_refs": [
                            *screen.source_refs,
                            "network_evidence.json",
                        ],
                        "evidence_ids": [
                            *screen.evidence_ids,
                            evidence_id,
                        ],
                    }
                )
            )

        attached_routes = {screen.route for screen in screens}
        omitted += sum(
            len(values) for route, values in by_route.items() if route not in attached_routes
        )

        source_artifacts = list(knowledge.source_artifacts)
        if "network_evidence.json" not in source_artifacts:
            source_artifacts.append("network_evidence.json")
        source_hashes = dict(knowledge.source_artifact_hashes)
        source_hashes["network_evidence.json"] = container_hash
        statistics = dict(knowledge.statistics)
        statistics["evidence"] = len(evidence)

        updated = knowledge.model_copy(
            update={
                "screens": screens,
                "evidence": evidence,
                "source_artifacts": source_artifacts,
                "source_artifact_hashes": source_hashes,
                "statistics": statistics,
            }
        )
        return CanonicalNetworkEvidenceResult(
            knowledge=updated,
            observation_count=attached_observations,
            screen_count=attached_screens,
            sensitive_exclusions=sensitive_exclusions,
            omitted_observations=omitted,
        )

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CanonicalNetworkEvidenceError("network_evidence.json está corrupto") from exc
        if not isinstance(payload, dict):
            raise CanonicalNetworkEvidenceError("network_evidence.json debe contener un objeto")
        observations = payload.get("observations", [])
        if not isinstance(observations, list):
            raise CanonicalNetworkEvidenceError("network_evidence.observations debe ser una lista")
        return payload

    @staticmethod
    def _validate_capture_policy(payload: dict[str, Any]) -> None:
        policy = payload.get("capture_policy")
        if not isinstance(policy, dict):
            raise CanonicalNetworkEvidenceError("network_evidence no declara capture_policy")
        forbidden_true = (
            "bodies_captured",
            "headers_captured",
            "query_values_captured",
        )
        if any(bool(policy.get(key)) for key in forbidden_true):
            raise CanonicalNetworkEvidenceError(
                "network_evidence contiene una política de captura insegura"
            )

    @classmethod
    def _clean_observation(cls, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        route = normalize_route(raw.get("screen_route"))
        endpoint = str(raw.get("endpoint_path") or "").strip()
        if not route.startswith("/") or not endpoint.startswith("/"):
            return None
        if cls._looks_sensitive(raw):
            return None

        query_keys = [
            str(value)[:64]
            for value in (raw.get("query_keys") or [])
            if isinstance(value, str) and value.strip() and not contains_sensitive(value)
        ][:32]
        status_codes = [
            int(value)
            for value in (raw.get("status_codes") or [])
            if isinstance(value, int) and 100 <= value <= 599
        ][:16]
        return {
            "screen_route": route,
            "method": str(raw.get("method") or "GET")[:12],
            "endpoint_path": endpoint[:500],
            "origin_id": str(raw.get("origin_id") or "")[:80],
            "origin_kind": str(raw.get("origin_kind") or "")[:24],
            "resource_type": str(raw.get("resource_type") or "")[:24],
            "query_keys": sorted(set(query_keys)),
            "status_codes": sorted(set(status_codes)),
            "observed_count": max(1, int(raw.get("observed_count") or 1)),
        }

    @staticmethod
    def _looks_sensitive(raw: dict[str, Any]) -> bool:
        endpoint = raw.get("endpoint_path")
        if contains_sensitive(endpoint):
            return True
        for value in raw.get("query_keys") or []:
            if contains_sensitive(value):
                return True
        return False

    @staticmethod
    def _observation_sort_key(item: dict[str, Any]) -> tuple[str, ...]:
        return (
            item["method"],
            item["origin_id"],
            item["endpoint_path"],
            item["resource_type"],
        )

    @staticmethod
    def _metadata(observations: list[dict[str, Any]]) -> dict[str, Any]:
        endpoints = sorted({item["endpoint_path"] for item in observations})
        methods = sorted({item["method"] for item in observations})
        resource_types = sorted({item["resource_type"] for item in observations})
        origin_kinds = sorted({item["origin_kind"] for item in observations})
        query_keys = sorted({value for item in observations for value in item["query_keys"]})
        statuses = sorted({value for item in observations for value in item["status_codes"]})
        return {
            "observation_count": sum(item["observed_count"] for item in observations),
            "endpoint_count": len(endpoints),
            "endpoint_paths": " | ".join(endpoints)[:2000],
            "methods": ",".join(methods)[:200],
            "resource_types": ",".join(resource_types)[:200],
            "origin_kinds": ",".join(origin_kinds)[:200],
            "query_keys": ",".join(query_keys)[:500],
            "status_codes": ",".join(str(value) for value in statuses)[:200],
            "headers_captured": False,
            "bodies_captured": False,
            "query_values_captured": False,
        }

    def _relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return path.as_posix()
