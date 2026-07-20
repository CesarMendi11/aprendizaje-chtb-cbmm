from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus, SyncTarget
from src.database.models import ERPSystemRecord, KnowledgeItem, KnowledgeVersionRecord
from src.database.repositories import SyncJobRepository
from src.graph.mapper import GraphMapper, GraphMappingError
from src.knowledge.canonical.privacy import contains_sensitive, sanitize_text

from .effective_knowledge_service import EffectiveKnowledgeService

SAFE_LABEL_FIELDS = ("name", "title", "label", "normalized_name", "normalized_title", "route")
SCOPES = ("core", "screen-complete")
ELIGIBLE_STATUSES = {"approved", "corrected"}


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


@dataclass(frozen=True)
class OmittedItem:
    canonical_id: str
    entity_type: str
    safe_label: str
    reason_code: str

    def as_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


class Neo4jSubsetPlanner:
    def __init__(self, session: Session, *, mapper: GraphMapper | None = None):
        self.session = session
        self.mapper = mapper or GraphMapper()
        self.effective = EffectiveKnowledgeService(session)
        self.jobs = SyncJobRepository(session)

    def plan(self, screen_route: str, *, scope: str = "core") -> dict[str, Any]:
        if scope not in SCOPES:
            raise SubsetPlanningError("Scope de planificación no soportado")
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
        omitted: list[tuple[KnowledgeItem, str]] = []
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

        if scope == "screen-complete":
            selected = [item for item in selected if item.entity_type != "ui_state"]
            selected.extend(
                sorted(states, key=lambda item: self._complete_state_key(item, payload(item)))
            )

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

        if scope == "screen-complete":
            self._extend_screen_complete(
                items=items,
                selected=selected,
                omitted=omitted,
                payload_getter=payload,
                screen=screen,
                states=states,
                table=table,
            )

        planned, privacy_errors, mapper_errors, ready_ids = self._validate(
            selected, payload, erp.id, version.knowledge_version
        )
        relation_counts, skipped_relationships, skipped_reasons = self._relationships(
            selected, payload, ready_ids
        )
        job = self.jobs.get(version.id, SyncTarget.NEO4J)
        by_type = Counter(item.entity_type for item in selected)
        omitted_by_type = Counter(item.entity_type for item, _ in omitted)
        omitted_reasons = Counter(reason for _, reason in omitted)
        status_counts = Counter(str(item.current_review_status) for item in selected)
        return {
            "erp_id": erp.id,
            "knowledge_version": version.knowledge_version,
            "screen_route": screen_route,
            "screen_title": self._safe_label(screen_payload),
            "scope": scope,
            "current_total_items": len(items),
            "candidate_items": len(selected) + len(omitted),
            "selected_items": [item.as_dict() for item in planned],
            "selected_items_by_type": dict(sorted(by_type.items())),
            "omitted_items": [
                OmittedItem(
                    canonical_id=item.canonical_id,
                    entity_type=item.entity_type,
                    safe_label=self._safe_label(payload(item)),
                    reason_code=reason,
                ).as_dict()
                for item, reason in omitted
            ],
            "omitted_items_by_type": dict(sorted(omitted_by_type.items())),
            "omitted_reasons": dict(sorted(omitted_reasons.items())),
            "expected_nodes": len(ready_ids),
            "expected_relationships": dict(sorted(relation_counts.items())),
            "expected_relationships_total": sum(relation_counts.values()),
            "skipped_relationships": skipped_relationships,
            "skipped_reasons": dict(sorted(skipped_reasons.items())),
            "missing_dependencies": sorted(set(missing)),
            "privacy_errors": dict(sorted(privacy_errors.items())),
            "mapper_errors": dict(sorted(mapper_errors.items())),
            "already_eligible_items": sum(
                status_counts[status] for status in ELIGIBLE_STATUSES
            ),
            "pending_review_items": status_counts["pending_review"],
            "current_sync_job": {
                "id": str(job.id),
                "status": str(job.status),
                "attempt_count": job.attempt_count,
            }
            if job
            else None,
        }

    def _extend_screen_complete(
        self, *, items, selected, omitted, payload_getter, screen, states, table
    ):
        screen_id = screen.canonical_id
        state_ids = {item.canonical_id for item in states}
        selected_ids = {item.canonical_id for item in selected}

        def add_candidates(entity_type, key):
            candidates = []
            for item in items:
                if item.entity_type != entity_type:
                    continue
                item_payload = payload_getter(item)
                claimed = item_payload.get("screen_id") == screen_id
                parent_claimed = item.parent_canonical_id in {screen_id, *state_ids}
                if not (claimed or parent_claimed):
                    continue
                if self._is_global_region(item_payload.get("region")):
                    omitted.append((item, "global_scope_entity"))
                elif item_payload.get("screen_id") not in {None, screen_id}:
                    omitted.append((item, "screen_mismatch"))
                elif item.parent_canonical_id not in {None, screen_id, *state_ids}:
                    omitted.append((item, "parent_not_selected"))
                else:
                    candidates.append(item)
            for item in sorted(
                candidates, key=lambda candidate: key(candidate, payload_getter(candidate))
            ):
                if item.canonical_id not in selected_ids:
                    selected.append(item)
                    selected_ids.add(item.canonical_id)

        add_candidates("field", self._field_key)
        add_candidates("control", self._control_key)

        links = []
        for item in items:
            if item.entity_type != "link":
                continue
            item_payload = payload_getter(item)
            if item_payload.get("screen_id") != screen_id and item.parent_canonical_id not in {
                screen_id,
                *state_ids,
            }:
                continue
            if self._is_global_region(item_payload.get("region")):
                omitted.append((item, "global_scope_entity"))
            elif item_payload.get("screen_id") not in {None, screen_id}:
                omitted.append((item, "screen_mismatch"))
            elif not self._safe_local_target(item_payload.get("target_route")):
                omitted.append((item, "unsafe_link_target"))
            else:
                links.append(item)
        for item in sorted(
            links,
            key=lambda candidate: self._link_key(candidate, payload_getter(candidate)),
        ):
            selected.append(item)
            selected_ids.add(item.canonical_id)

        events = []
        for item in items:
            if item.entity_type != "event":
                continue
            item_payload = payload_getter(item)
            references = {
                item_payload.get("screen_id"),
                item_payload.get("source_state_id"),
                item_payload.get("control_id"),
                item_payload.get("link_id"),
            }
            if not references.intersection({screen_id, *state_ids, *selected_ids}) and (
                item.parent_canonical_id not in {screen_id, *state_ids}
            ):
                continue
            source_state = item_payload.get("source_state_id")
            if self._is_global_region(item_payload.get("region")):
                omitted.append((item, "global_scope_entity"))
            elif source_state and source_state not in state_ids:
                omitted.append((item, "event_source_not_selected"))
            elif item_payload.get("control_id") and (
                item_payload.get("control_id") not in selected_ids
            ):
                omitted.append((item, "event_source_not_selected"))
            elif item_payload.get("link_id") and item_payload.get("link_id") not in selected_ids:
                omitted.append((item, "event_source_not_selected"))
            elif item_payload.get("screen_id") not in {None, screen_id}:
                omitted.append((item, "screen_mismatch"))
            elif not references.intersection(selected_ids):
                omitted.append((item, "orphan_event"))
            else:
                events.append(item)
        for item in sorted(
            events,
            key=lambda candidate: self._event_key(candidate, payload_getter(candidate)),
        ):
            selected.append(item)
            selected_ids.add(item.canonical_id)

        transitions = []
        for item in items:
            if item.entity_type != "transition":
                continue
            item_payload = payload_getter(item)
            source = item_payload.get("source_state_id")
            target = item_payload.get("target_state_id")
            if source not in state_ids and target not in state_ids:
                continue
            if source not in state_ids or target not in state_ids:
                omitted.append((item, "cross_screen_transition"))
            elif item_payload.get("event_id") and item_payload.get("event_id") not in selected_ids:
                omitted.append((item, "transition_event_not_selected"))
            else:
                transitions.append(item)
        for item in sorted(
            transitions,
            key=lambda candidate: self._transition_key(candidate, payload_getter(candidate)),
        ):
            selected.append(item)
            selected_ids.add(item.canonical_id)

        for item in items:
            if (
                item.entity_type == "table"
                and payload_getter(item).get("screen_id") == screen_id
                and (table is None or item.canonical_id != table.canonical_id)
            ):
                omitted.append((item, "non_primary_table"))

        omitted.sort(key=lambda entry: (entry[0].entity_type, entry[0].canonical_id, entry[1]))

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
    def _complete_state_key(item, payload):
        depth = payload.get("depth")
        depth = depth if isinstance(depth, int) else 2**31
        return (depth != 0, depth, not bool(payload.get("is_route_root")), item.canonical_id)

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

    @staticmethod
    def _ordered_position(payload):
        position = payload.get("position", payload.get("index"))
        return position if isinstance(position, int) else 2**31

    @classmethod
    def _field_key(cls, item, payload):
        return (
            cls._ordered_position(payload),
            str(payload.get("normalized_label") or "\uffff"),
            str(payload.get("normalized_name") or "\uffff"),
            item.canonical_id,
        )

    @classmethod
    def _control_key(cls, item, payload):
        return (
            cls._ordered_position(payload),
            str(payload.get("control_type") or "\uffff"),
            str(payload.get("normalized_label") or "\uffff"),
            item.canonical_id,
        )

    @classmethod
    def _link_key(cls, item, payload):
        return (
            cls._ordered_position(payload),
            str(payload.get("normalized_label") or "\uffff"),
            str(payload.get("route") or payload.get("target_route") or "\uffff"),
            item.canonical_id,
        )

    @staticmethod
    def _event_key(item, payload):
        return (
            str(payload.get("event_type") or payload.get("category") or "\uffff"),
            str(
                payload.get("source_state_id")
                or payload.get("control_id")
                or payload.get("link_id")
                or payload.get("screen_id")
                or "\uffff"
            ),
            item.canonical_id,
        )

    @staticmethod
    def _transition_key(item, payload):
        return (
            str(payload.get("source_state_id") or "\uffff"),
            str(payload.get("target_state_id") or "\uffff"),
            str(payload.get("event_id") or "\uffff"),
            item.canonical_id,
        )

    @staticmethod
    def _safe_local_target(value):
        if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
            return False
        parsed = urlsplit(value)
        return not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment

    @staticmethod
    def _is_global_region(value):
        if not isinstance(value, str):
            return False
        return value.casefold() in {
            "global_navigation",
            "header",
            "footer",
            "sidebar",
            "navigation",
        }

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
        skipped = Counter()
        for item in selected:
            if item.canonical_id not in ready_ids:
                continue
            for relation_type, source_id, target_id in self.mapper.relationship_candidates(
                item.entity_type, payload_getter(item)
            ):
                if source_id in ready_ids and target_id in ready_ids:
                    counts[relation_type] += 1
                else:
                    skipped["endpoint_not_selected"] += 1
        return counts, sum(skipped.values()), skipped

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
