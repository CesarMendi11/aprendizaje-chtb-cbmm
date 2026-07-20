from __future__ import annotations

import hashlib
from typing import Any

from src.knowledge.canonical.privacy import contains_sensitive

from .models import GraphNode, GraphRelationship

MANAGED_BY = "erp_assistant"
LABELS = {
    "erp_system": "ERPSystem",
    "module": "Module",
    "screen": "Screen",
    "ui_state": "UIState",
    "field": "Field",
    "control": "Control",
    "table": "Table",
    "table_column": "TableColumn",
    "link": "Link",
    "event": "Event",
    "transition": "Transition",
    "evidence": "Evidence",
}
ALLOWED_PROPERTIES = {
    "erp_system": ("slug", "name", "profile_name", "adapter"),
    "module": ("name", "normalized_name", "route_prefix", "description"),
    "screen": ("title", "normalized_title", "route", "description"),
    "ui_state": ("route", "depth", "title", "is_route_root"),
    "field": (
        "label",
        "normalized_label",
        "input_type",
        "placeholder",
        "required",
        "readonly",
        "disabled",
    ),
    "control": (
        "label",
        "normalized_label",
        "control_type",
        "event_category",
        "safety_decision",
        "mutative",
        "target_route",
    ),
    "table": ("name", "normalized_name"),
    "table_column": ("name", "normalized_name", "position"),
    "link": ("label", "normalized_label", "target_route"),
    "event": ("label", "normalized_label", "category", "policy_decision", "mutative"),
    "transition": ("category", "changed", "route_changed", "depth", "observed"),
    "evidence": ("evidence_type", "artifact_path", "source_entity_type"),
}


class GraphMappingError(ValueError):
    pass


def node_key(erp_id: str, knowledge_version: str, canonical_id: str) -> str:
    return hashlib.sha256(f"{erp_id}\0{knowledge_version}\0{canonical_id}".encode()).hexdigest()


class GraphMapper:
    def projected_payload_properties(
        self, entity_type: str, payload: dict[str, Any]
    ) -> dict[str, str | bool | int | float]:
        """Return the canonical payload properties eligible for Neo4j projection."""
        if entity_type not in ALLOWED_PROPERTIES:
            raise GraphMappingError("Tipo canónico no soportado")
        projected: dict[str, str | bool | int | float] = {}
        for name in ALLOWED_PROPERTIES[entity_type]:
            value = payload.get(name)
            if value is None:
                continue
            if not isinstance(value, (str, bool, int, float)):
                raise GraphMappingError("Propiedad canónica no escalar")
            projected[name] = value
        return projected

    def map_node(
        self,
        *,
        entity_type: str,
        payload: dict[str, Any],
        content_hash: str,
        review_status: str,
        erp_id: str,
        knowledge_version: str,
        projected_at: str,
    ) -> GraphNode:
        if entity_type not in LABELS:
            raise GraphMappingError("Tipo canónico no soportado")
        if review_status not in {"approved", "corrected"}:
            raise GraphMappingError("Estado de revisión no elegible")
        canonical_id = payload.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            raise GraphMappingError("Entidad sin canonical_id")
        properties: dict[str, Any] = {
            "node_key": node_key(erp_id, knowledge_version, canonical_id),
            "canonical_id": canonical_id,
            "erp_id": erp_id,
            "knowledge_version": knowledge_version,
            "entity_type": entity_type,
            "managed_by": MANAGED_BY,
            "content_hash": content_hash,
            "review_status": review_status,
            "projected_at": projected_at,
        }
        for name, value in self.projected_payload_properties(entity_type, payload).items():
            if isinstance(value, str) and contains_sensitive(value):
                raise GraphMappingError("Propiedad canónica sensible")
            properties[name] = value
        return GraphNode(properties["node_key"], LABELS[entity_type], properties)

    def relationship_candidates(self, entity_type: str, payload: dict[str, Any]):
        cid = payload.get("id")
        candidates: list[tuple[str, str, str]] = []

        def add(rel_type, source, target):
            if source and target:
                candidates.append((rel_type, str(source), str(target)))

        if entity_type == "module":
            add("HAS_MODULE", payload.get("erp_id"), cid)
        elif entity_type == "screen":
            add("HAS_SCREEN", payload.get("erp_id"), cid)
            add("HAS_SCREEN", payload.get("module_id"), cid)
        elif entity_type == "ui_state":
            add("HAS_STATE", payload.get("screen_id"), cid)
        elif entity_type == "field":
            add("HAS_FIELD", payload.get("screen_id"), cid)
        elif entity_type == "control":
            add("HAS_CONTROL", payload.get("screen_id"), cid)
        elif entity_type == "table":
            add("HAS_TABLE", payload.get("screen_id"), cid)
        elif entity_type == "table_column":
            add("HAS_COLUMN", payload.get("table_id"), cid)
        elif entity_type == "link":
            add("HAS_LINK", payload.get("screen_id"), cid)
            add("TARGETS", cid, payload.get("target_screen_id"))
        elif entity_type == "event":
            add("HAS_EVENT", payload.get("screen_id"), cid)
            add("FROM_STATE", cid, payload.get("source_state_id"))
        elif entity_type == "transition":
            add("FROM_STATE", cid, payload.get("source_state_id"))
            add("TO_STATE", cid, payload.get("target_state_id"))
            add("TRIGGERED_BY", cid, payload.get("event_id"))
        elif entity_type == "evidence" and payload.get("source_entity_type") == "screen":
            add("HAS_EVIDENCE", payload.get("source_entity_id"), cid)
        return candidates

    def map_relationship(
        self,
        relationship_type: str,
        source_id: str,
        target_id: str,
        *,
        erp_id: str,
        knowledge_version: str,
    ) -> GraphRelationship:
        source = node_key(erp_id, knowledge_version, source_id)
        target = node_key(erp_id, knowledge_version, target_id)
        key = hashlib.sha256(f"{source}\0{relationship_type}\0{target}".encode()).hexdigest()
        return GraphRelationship(
            key,
            relationship_type,
            source,
            target,
            {
                "relationship_key": key,
                "managed_by": MANAGED_BY,
                "erp_id": erp_id,
                "knowledge_version": knowledge_version,
            },
        )
