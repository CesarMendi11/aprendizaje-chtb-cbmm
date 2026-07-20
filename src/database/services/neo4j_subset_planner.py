from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus, SyncTarget
from src.database.models import ERPSystemRecord, KnowledgeItem, KnowledgeVersionRecord
from src.database.repositories import SyncJobRepository
from src.graph.mapper import GraphMapper, GraphMappingError
from src.knowledge.canonical.privacy import contains_sensitive, sanitize_text

from .effective_knowledge_service import EffectiveKnowledgeService

SAFE_LABEL_FIELDS = ("name", "title", "label", "normalized_name", "normalized_title", "route")


class SubsetPlanningError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedItem:
    item_id: str
    canonical_id: str
    entity_type: str
    current_review_status: str
    safe_label: str
    parent_canonical_id: str | None
    content_hash: str
    mapper_ready: bool
    privacy_safe: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Neo4jSubsetPlanner:
    def __init__(self, session: Session, *, mapper: GraphMapper | None = None):
        self.session = session
        self.mapper = mapper or GraphMapper()
        self.effective = EffectiveKnowledgeService(session)
        self.jobs = SyncJobRepository(session)

    def plan(self, screen_route: str) -> dict[str, Any]:
        version = self._active_version()
        erp = self.session.get(ERPSystemRecord, version.erp_id)
        if not erp:
            raise SubsetPlanningError("ERP de la versión activa no encontrado")
        items = list(
            self.session.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.knowledge_version_id == version.id)
                .order_by(KnowledgeItem.entity_type, KnowledgeItem.canonical_id)
            )
        )
        screen_matches = [
            item for item in items if item.entity_type == "screen" and item.route == screen_route
        ]
        if not screen_matches:
            raise SubsetPlanningError("No existe una pantalla activa para la route solicitada")
        if len(screen_matches) != 1:
            raise SubsetPlanningError("Existen varias pantallas activas para la route solicitada")
        screen = screen_matches[0]
        payload_cache: dict[str, dict[str, Any]] = {}

        def payload(item: KnowledgeItem) -> dict[str, Any]:
            if item.canonical_id not in payload_cache:
                payload_cache[item.canonical_id] = self.effective.describe(item.id)[
                    "effective_payload"
                ]
            return payload_cache[item.canonical_id]

        selected: list[KnowledgeItem] = []
        missing: list[str] = []
        erp_item = self._exact(items, "erp_system", erp.id, "erp_system")
        selected.append(erp_item)

        screen_payload = payload(screen)
        module_id = screen_payload.get("module_id")
        module = self._optional_exact(items, "module", module_id)
        if module:
            self._require_parent(module, erp.id)
            selected.append(module)
            self._require_parent(screen, module.canonical_id)
        else:
            missing.append("module")
        selected.append(screen)

        states = [
            item
            for item in items
            if item.entity_type == "ui_state"
            and payload(item).get("screen_id") == screen.canonical_id
        ]
        state = (
            min(states, key=lambda item: self._state_key(item, payload(item))) if states else None
        )
        if state:
            self._require_parent(state, screen.canonical_id)
            selected.append(state)
        else:
            missing.append("ui_state")

        tables = [
            item
            for item in items
            if item.entity_type == "table" and payload(item).get("screen_id") == screen.canonical_id
        ]
        table = (
            min(tables, key=lambda item: self._table_key(item, payload(item))) if tables else None
        )
        if table:
            self._require_parent(table, screen.canonical_id)
            selected.append(table)
            columns = [
                item
                for item in items
                if item.entity_type == "table_column"
                and payload(item).get("table_id") == table.canonical_id
            ]
            for column in sorted(columns, key=lambda item: self._column_key(item, payload(item))):
                self._require_parent(column, table.canonical_id)
                selected.append(column)
            if not columns:
                missing.append("table_column")
        else:
            missing.extend(("table", "table_column"))

        planned, privacy_errors, mapper_errors, ready_ids = self._validate(
            selected, payload, erp.id, version.knowledge_version
        )
        relation_counts = self._relationships(selected, payload, ready_ids)
        job = self.jobs.get(version.id, SyncTarget.NEO4J)
        by_type = Counter(item.entity_type for item in selected)
        return {
            "erp_id": erp.id,
            "knowledge_version": version.knowledge_version,
            "screen_route": screen_route,
            "screen_title": self._safe_label(screen_payload),
            "current_total_items": len(items),
            "selected_items": [item.as_dict() for item in planned],
            "selected_items_by_type": dict(sorted(by_type.items())),
            "expected_nodes": len(ready_ids),
            "expected_relationships": dict(sorted(relation_counts.items())),
            "missing_dependencies": sorted(set(missing)),
            "privacy_errors": dict(sorted(privacy_errors.items())),
            "mapper_errors": dict(sorted(mapper_errors.items())),
            "current_sync_job": {
                "id": str(job.id),
                "status": str(job.status),
                "attempt_count": job.attempt_count,
            }
            if job
            else None,
        }

    def _active_version(self):
        versions = list(
            self.session.scalars(
                select(KnowledgeVersionRecord).where(
                    KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE
                )
            )
        )
        if len(versions) != 1:
            raise SubsetPlanningError("No existe una única versión activa")
        return versions[0]

    @staticmethod
    def _exact(items, entity_type, canonical_id, dependency):
        matches = [
            item
            for item in items
            if item.entity_type == entity_type and item.canonical_id == canonical_id
        ]
        if len(matches) != 1:
            raise SubsetPlanningError(f"Dependencia no resoluble: {dependency}")
        return matches[0]

    @classmethod
    def _optional_exact(cls, items, entity_type, canonical_id):
        if not canonical_id:
            return None
        matches = [
            item
            for item in items
            if item.entity_type == entity_type and item.canonical_id == canonical_id
        ]
        if len(matches) > 1:
            raise SubsetPlanningError(f"Dependencia duplicada: {entity_type}")
        return matches[0] if matches else None

    @staticmethod
    def _require_parent(item, expected):
        if item.parent_canonical_id != expected:
            raise SubsetPlanningError(f"Dependencia parent inconsistente: {item.entity_type}")

    @staticmethod
    def _state_key(item, payload):
        depth = payload.get("depth")
        depth = depth if isinstance(depth, int) else 2**31
        return (depth != 0, depth, item.canonical_id)

    @staticmethod
    def _table_key(item, payload):
        position = payload.get("position", payload.get("index"))
        position = position if isinstance(position, int) else 2**31
        return (position, str(payload.get("normalized_name") or ""), item.canonical_id)

    @staticmethod
    def _column_key(item, payload):
        position = payload.get("position")
        position = position if isinstance(position, int) else 2**31
        return (position, str(payload.get("normalized_name") or ""), item.canonical_id)

    def _validate(self, selected, payload_getter, erp_id, knowledge_version):
        planned = []
        privacy_errors = Counter()
        mapper_errors = Counter()
        ready_ids = set()
        stamp = datetime.now(timezone.utc).isoformat()
        for item in selected:
            effective = payload_getter(item)
            safe_label = self._safe_label(effective)
            try:
                projected = self.mapper.projected_payload_properties(item.entity_type, effective)
                privacy_safe = not self._contains_sensitive(projected) and not contains_sensitive(
                    safe_label
                )
            except GraphMappingError:
                privacy_safe = False
            if not privacy_safe:
                privacy_errors["sensitive_value_detected"] += 1
            mapper_ready = False
            try:
                self.mapper.map_node(
                    entity_type=item.entity_type,
                    payload=effective,
                    content_hash=item.content_hash,
                    review_status="approved",
                    erp_id=erp_id,
                    knowledge_version=knowledge_version,
                    projected_at=stamp,
                )
                mapper_ready = True
            except GraphMappingError:
                mapper_errors["mapping_rejected"] += 1
            if privacy_safe and mapper_ready:
                ready_ids.add(item.canonical_id)
            planned.append(
                PlannedItem(
                    item_id=str(item.id),
                    canonical_id=item.canonical_id,
                    entity_type=item.entity_type,
                    current_review_status=str(item.current_review_status),
                    safe_label=safe_label,
                    parent_canonical_id=item.parent_canonical_id,
                    content_hash=item.content_hash,
                    mapper_ready=mapper_ready,
                    privacy_safe=privacy_safe,
                )
            )
        return planned, privacy_errors, mapper_errors, ready_ids

    def _relationships(self, selected, payload_getter, ready_ids):
        counts = Counter()
        for item in selected:
            if item.canonical_id not in ready_ids:
                continue
            for relation_type, source_id, target_id in self.mapper.relationship_candidates(
                item.entity_type, payload_getter(item)
            ):
                if source_id in ready_ids and target_id in ready_ids:
                    counts[relation_type] += 1
        return counts

    @staticmethod
    def _safe_label(payload):
        for field in SAFE_LABEL_FIELDS:
            value = payload.get(field)
            if value:
                clean, detections = sanitize_text(value, 160)
                if clean and not detections:
                    return clean
        return ""

    @classmethod
    def _contains_sensitive(cls, value):
        if isinstance(value, dict):
            return any(cls._contains_sensitive(item) for item in value.values())
        if isinstance(value, list):
            return any(cls._contains_sensitive(item) for item in value)
        return isinstance(value, str) and contains_sensitive(value)
