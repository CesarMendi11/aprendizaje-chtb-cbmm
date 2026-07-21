from __future__ import annotations

import json
import uuid
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.schemas import (
    ColumnEvidence,
    ControlEvidence,
    EventEvidence,
    FieldEvidence,
    ModuleEvidence,
    ScreenEvidencePackage,
    TableEvidence,
    TransitionEvidence,
    UIStateEvidence,
)
from src.database.models import KnowledgeItem, KnowledgeVersionRecord
from src.database.services.effective_knowledge_service import EffectiveKnowledgeService
from src.database.services.semantic_payloads import canonical_json_hash
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import sanitize_text

MAX_FIELDS = 50
MAX_CONTROLS = 50
MAX_TABLES = 10
MAX_COLUMNS_PER_TABLE = 50
MAX_UI_STATES = 20
MAX_EVENTS = 30
MAX_TRANSITIONS = 30
MAX_EVIDENCE_IDS = 100
MAX_WARNINGS = 50
MAX_MAIN_CONTENT_CHARS = 8_000
MAX_PACKAGE_BYTES = 256_000
ELIGIBLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


class ScreenEvidenceError(Exception):
    pass


class EvidenceVersionNotFoundError(ScreenEvidenceError):
    pass


class EvidenceScreenNotFoundError(ScreenEvidenceError):
    pass


class EvidenceEntityTypeError(ScreenEvidenceError):
    pass


class EvidenceVersionMismatchError(ScreenEvidenceError):
    pass


class EvidenceScreenReviewError(ScreenEvidenceError):
    pass


class UnsafeScreenRouteError(ScreenEvidenceError):
    pass


class StructuralPayloadError(ScreenEvidenceError):
    pass


class StructuralRelationError(ScreenEvidenceError):
    pass


class EffectiveContentIntegrityError(ScreenEvidenceError):
    pass


class EvidencePackageTooLargeError(ScreenEvidenceError):
    pass


class ScreenEvidenceBuilder:
    def __init__(self, session: Session):
        self.session = session
        self.effective = EffectiveKnowledgeService(session)

    def build(self, knowledge_version_id, screen_knowledge_item_id) -> ScreenEvidencePackage:
        version = self._version(knowledge_version_id)
        screen = self._screen(screen_knowledge_item_id, version.id)
        items = list(
            self.session.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.knowledge_version_id == version.id)
                .order_by(KnowledgeItem.entity_type, KnowledgeItem.canonical_id)
            )
        )
        eligible = {
            item.canonical_id: item for item in items if item.current_review_status in ELIGIBLE
        }
        warnings: list[str] = []
        screen_payload = self._effective(screen)
        payloads = {screen.canonical_id: screen_payload}
        module_id = self._id(screen_payload.get("module_id"))
        module_item = eligible.get(module_id)
        if not module_id or not module_item or module_item.entity_type != "module":
            raise StructuralRelationError("La pantalla no tiene un módulo aprobado válido")
        module_payload = self._effective(module_item)
        payloads[module_id] = module_payload
        module_name = self._safe(module_payload.get("name"), warnings, "module.name", required=True)
        title = self._safe(screen_payload.get("title"), warnings, "screen.title", required=True)
        route = self._route(screen_payload.get("route") or screen.route)
        fields = self._relation_candidates(
            items, "field", "screen_id", {screen.canonical_id}, warnings
        )
        controls = self._relation_candidates(
            items, "control", "screen_id", {screen.canonical_id}, warnings
        )
        tables = self._relation_candidates(
            items, "table", "screen_id", {screen.canonical_id}, warnings
        )
        states = self._relation_candidates(
            items, "ui_state", "screen_id", {screen.canonical_id}, warnings
        )
        events = self._relation_candidates(
            items, "event", "screen_id", {screen.canonical_id}, warnings
        )
        for item, payload in [*fields, *controls, *tables, *states, *events]:
            payloads[item.canonical_id] = payload
        field_items = [item for item, _ in fields]
        control_items = [item for item, _ in controls]
        table_items = [item for item, _ in tables]
        state_items = [item for item, _ in states]
        event_items = [item for item, _ in events]
        state_ids = {item.canonical_id for item in state_items}
        control_ids = {item.canonical_id for item in control_items}
        event_items = self._valid_events(event_items, payloads, state_ids, warnings)
        transition_pairs = self._transition_candidates(items, state_ids, control_ids, warnings)
        transitions = [item for item, _ in transition_pairs]
        for item, payload in transition_pairs:
            payloads[item.canonical_id] = payload

        field_dtos = self._limited(
            self._dedupe_label(
                [
                    FieldEvidence(
                        field_id=item.canonical_id,
                        label=self._safe(
                            payloads[item.canonical_id].get("label"),
                            warnings,
                            f"field:{item.canonical_id}",
                        ),
                        input_type=self._optional(
                            payloads[item.canonical_id].get("input_type"),
                            warnings,
                            f"field:{item.canonical_id}:input_type",
                        ),
                        required=bool(payloads[item.canonical_id].get("required", False)),
                        readonly=bool(payloads[item.canonical_id].get("readonly", False)),
                    )
                    for item in field_items
                    if self._safe(
                        payloads[item.canonical_id].get("label"),
                        warnings,
                        f"field:{item.canonical_id}",
                    )
                ],
                "label",
            ),
            MAX_FIELDS,
            warnings,
            "fields",
        )
        control_dtos = self._limited(
            self._dedupe_label(
                [
                    ControlEvidence(
                        control_id=item.canonical_id,
                        label=self._safe(
                            payloads[item.canonical_id].get("label"),
                            warnings,
                            f"control:{item.canonical_id}",
                        ),
                        control_type=self._optional(
                            payloads[item.canonical_id].get("control_type"),
                            warnings,
                            f"control:{item.canonical_id}:type",
                        ),
                        mutative=bool(payloads[item.canonical_id].get("mutative", False)),
                        safety_decision=self._optional(
                            payloads[item.canonical_id].get("safety_decision"),
                            warnings,
                            f"control:{item.canonical_id}:safety",
                        ),
                    )
                    for item in control_items
                    if self._safe(
                        payloads[item.canonical_id].get("label"),
                        warnings,
                        f"control:{item.canonical_id}",
                    )
                ],
                "label",
            ),
            MAX_CONTROLS,
            warnings,
            "controls",
        )
        table_dtos = []
        selected_columns = []
        for table in table_items[:MAX_TABLES]:
            payload = payloads[table.canonical_id]
            name = self._safe(
                payload.get("name") or "Tabla", warnings, f"table:{table.canonical_id}"
            )
            column_pairs = self._relation_candidates(
                items, "table_column", "table_id", {table.canonical_id}, warnings
            )
            columns = [item for item, _ in column_pairs]
            for column, column_payload in column_pairs:
                payloads[column.canonical_id] = column_payload
            selected_columns.extend(columns)
            column_dtos = []
            for column in sorted(columns, key=lambda value: value.canonical_id)[
                :MAX_COLUMNS_PER_TABLE
            ]:
                label = self._safe(
                    payloads[column.canonical_id].get("name"),
                    warnings,
                    f"column:{column.canonical_id}",
                )
                if label:
                    column_dtos.append(ColumnEvidence(column_id=column.canonical_id, label=label))
            column_dtos = self._dedupe_label(column_dtos, "label")
            if len(columns) > MAX_COLUMNS_PER_TABLE:
                self._warn(warnings, f"limit_exceeded:columns:{table.canonical_id}")
            table_dtos.append(
                TableEvidence(table_id=table.canonical_id, name=name, columns=column_dtos)
            )
        if len(table_items) > MAX_TABLES:
            self._warn(warnings, "limit_exceeded:tables")

        state_dtos = self._limited(
            self._dedupe_label(
                [
                    UIStateEvidence(
                        state_id=item.canonical_id,
                        title=self._safe(
                            payloads[item.canonical_id].get("title"),
                            warnings,
                            f"state:{item.canonical_id}",
                        ),
                        depth=self._depth(payloads[item.canonical_id].get("depth")),
                    )
                    for item in state_items
                    if self._safe(
                        payloads[item.canonical_id].get("title"),
                        warnings,
                        f"state:{item.canonical_id}",
                    )
                ],
                "title",
            ),
            MAX_UI_STATES,
            warnings,
            "ui_states",
        )
        event_dtos = self._limited(
            self._dedupe_label(
                [
                    EventEvidence(
                        event_id=item.canonical_id,
                        label=self._safe(
                            payloads[item.canonical_id].get("label"),
                            warnings,
                            f"event:{item.canonical_id}",
                        ),
                        category=self._safe(
                            payloads[item.canonical_id].get("category") or "unknown",
                            warnings,
                            f"event:{item.canonical_id}:category",
                        ),
                        policy_decision=self._safe(
                            payloads[item.canonical_id].get("policy_decision") or "unknown",
                            warnings,
                            f"event:{item.canonical_id}:policy",
                        ),
                        mutative=bool(payloads[item.canonical_id].get("mutative", False)),
                    )
                    for item in event_items
                    if self._safe(
                        payloads[item.canonical_id].get("label"),
                        warnings,
                        f"event:{item.canonical_id}",
                    )
                ],
                "label",
            ),
            MAX_EVENTS,
            warnings,
            "events",
        )
        transition_dtos = self._limited(
            [
                TransitionEvidence(
                    transition_id=item.canonical_id,
                    category=self._safe(
                        payloads[item.canonical_id].get("category") or "unknown",
                        warnings,
                        f"transition:{item.canonical_id}",
                    ),
                    source_state_id=self._valid_ref(
                        payloads[item.canonical_id].get("source_state_id"),
                        state_ids,
                        warnings,
                        item.canonical_id,
                        "source_state",
                    ),
                    target_state_id=self._valid_ref(
                        payloads[item.canonical_id].get("target_state_id"),
                        state_ids,
                        warnings,
                        item.canonical_id,
                        "target_state",
                    ),
                    trigger_control_id=self._valid_ref(
                        payloads[item.canonical_id].get("trigger_control_id"),
                        {item.control_id for item in control_dtos},
                        warnings,
                        item.canonical_id,
                        "trigger_control",
                    ),
                )
                for item in sorted(transitions, key=lambda value: value.canonical_id)
            ],
            MAX_TRANSITIONS,
            warnings,
            "transitions",
        )

        selected = [
            screen,
            module_item,
            *field_items,
            *control_items,
            *table_items,
            *selected_columns,
            *state_items,
            *event_items,
            *transitions,
        ]
        selected_ids = {item.canonical_id for item in selected}
        evidence_ids = self._evidence_ids(selected, payloads, items, selected_ids, warnings)
        main_text = self._main_text(
            module_name,
            title,
            field_dtos,
            control_dtos,
            table_dtos,
            state_dtos,
            event_dtos,
            warnings,
        )
        raw = {
            "schema_version": "1.0",
            "erp_id": version.erp_id,
            "knowledge_version_id": version.id,
            "knowledge_version": version.knowledge_version,
            "screen_id": screen.canonical_id,
            "screen_title": title,
            "screen_route": route,
            "module": ModuleEvidence(module_id=module_id, name=module_name),
            "fields": field_dtos,
            "controls": control_dtos,
            "tables": table_dtos,
            "ui_states": state_dtos,
            "events": event_dtos,
            "transitions": transition_dtos,
            "main_content_text": main_text,
            "evidence_ids": evidence_ids,
            "warnings": warnings[:MAX_WARNINGS],
        }
        canonical = ScreenEvidencePackage.model_validate({**raw, "evidence_hash": "0" * 64})
        hash_value = canonical_json_hash(
            canonical.model_dump(mode="json", exclude={"evidence_hash"})
        )
        package = canonical.model_copy(update={"evidence_hash": hash_value})
        if (
            len(
                json.dumps(
                    package.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
                ).encode()
            )
            > MAX_PACKAGE_BYTES
        ):
            raise EvidencePackageTooLargeError("El paquete de evidencia excede el límite seguro")
        return package

    def build_by_canonical_id(
        self, knowledge_version_id, screen_canonical_id: str
    ) -> ScreenEvidencePackage:
        version = self._version(knowledge_version_id)
        item = self.session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.knowledge_version_id == version.id,
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.canonical_id == screen_canonical_id,
            )
        )
        if item is None:
            raise EvidenceScreenNotFoundError("Pantalla no encontrada")
        return self.build(version.id, item.id)

    def _version(self, value):
        try:
            identifier = uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise EvidenceVersionNotFoundError("Versión no encontrada") from exc
        version = self.session.get(KnowledgeVersionRecord, identifier)
        if version is None:
            raise EvidenceVersionNotFoundError("Versión no encontrada")
        return version

    def _screen(self, value, version_id):
        try:
            identifier = uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise EvidenceScreenNotFoundError("Pantalla no encontrada") from exc
        item = self.session.get(KnowledgeItem, identifier)
        if item is None:
            raise EvidenceScreenNotFoundError("Pantalla no encontrada")
        if item.entity_type != "screen":
            raise EvidenceEntityTypeError("El item no es una pantalla")
        if item.knowledge_version_id != version_id:
            raise EvidenceVersionMismatchError("La pantalla pertenece a otra versión")
        if item.current_review_status not in ELIGIBLE:
            raise EvidenceScreenReviewError("La pantalla no está aprobada")
        return item

    def _effective(self, item):
        description = self.effective.describe(item.id)
        if (
            item.current_review_status == ReviewStatus.CORRECTED
            and not description["was_corrected"]
        ):
            raise EffectiveContentIntegrityError(
                "Un item corregido carece de historial consistente"
            )
        payload = description["effective_payload"]
        if not isinstance(payload, dict) or payload.get("id") != item.canonical_id:
            raise StructuralPayloadError("Payload estructural inconsistente")
        return payload

    def _relation_candidates(self, items, entity_type, key, targets, warnings):
        selected = []
        for item in items:
            if item.entity_type != entity_type:
                continue
            original_match = item.source_payload.get(key) in targets
            if item.current_review_status not in ELIGIBLE:
                if original_match:
                    self._warn(
                        warnings,
                        f"excluded_review_status:{entity_type}:{item.canonical_id}",
                    )
                continue
            if not original_match and item.current_review_status != ReviewStatus.CORRECTED:
                continue
            try:
                payload = self._effective(item)
            except EffectiveContentIntegrityError:
                if original_match:
                    raise
                self._warn(warnings, f"ignored_inconsistent_corrected:{entity_type}")
                continue
            if payload.get(key) in targets:
                selected.append((item, payload))
        return selected

    def _valid_events(self, events, payloads, state_ids, warnings):
        result = []
        for item in events:
            source = self._id(payloads[item.canonical_id].get("source_state_id"))
            if source and source not in state_ids:
                self._warn(warnings, f"invalid_relation:{item.canonical_id}:source_state")
                continue
            result.append(item)
        return result

    def _transition_candidates(self, items, state_ids, control_ids, warnings):
        result = []
        for item in items:
            if item.entity_type != "transition":
                continue
            original_match = (
                item.source_payload.get("source_state_id") in state_ids
                or item.source_payload.get("target_state_id") in state_ids
            )
            if item.current_review_status not in ELIGIBLE:
                if original_match:
                    self._warn(
                        warnings,
                        f"excluded_review_status:transition:{item.canonical_id}",
                    )
                continue
            if not original_match and item.current_review_status != ReviewStatus.CORRECTED:
                continue
            try:
                payload = self._effective(item)
            except EffectiveContentIntegrityError:
                if original_match:
                    raise
                self._warn(warnings, "ignored_inconsistent_corrected:transition")
                continue
            source = self._id(payload.get("source_state_id"))
            target = self._id(payload.get("target_state_id"))
            trigger = self._id(payload.get("trigger_control_id"))
            if source not in state_ids or target not in state_ids:
                if source in state_ids or target in state_ids:
                    self._warn(warnings, f"invalid_relation:{item.canonical_id}:state")
                continue
            if trigger and trigger not in control_ids:
                self._warn(warnings, f"invalid_relation:{item.canonical_id}:trigger_control")
                continue
            result.append((item, payload))
        return result

    @staticmethod
    def _id(value):
        return value.strip() if isinstance(value, str) and value.strip() else ""

    def _safe(self, value, warnings, field, required=False):
        if not isinstance(value, str) or not value.strip():
            if required:
                raise StructuralPayloadError("Falta una etiqueta estructural obligatoria")
            return ""
        clean, detections = sanitize_text(value, 501)
        if detections or not clean or len(clean) > 500:
            self._warn(warnings, f"excluded_sensitive:{field}")
            if required:
                raise StructuralPayloadError("Una etiqueta estructural obligatoria no es segura")
            return ""
        return clean

    def _optional(self, value, warnings, field):
        return self._safe(value, warnings, field) or None if value is not None else None

    @staticmethod
    def _depth(value):
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    def _limited(self, values, limit, warnings, name):
        unique = {value.model_dump_json(): value for value in values}
        ordered = sorted(unique.values(), key=lambda value: next(iter(value.model_dump().values())))
        if len(ordered) > limit:
            self._warn(warnings, f"limit_exceeded:{name}")
        return ordered[:limit]

    def _valid_ref(self, value, allowed, warnings, owner, kind):
        reference = self._id(value)
        if not reference:
            return None
        if reference not in allowed:
            self._warn(warnings, f"invalid_relation:{owner}:{kind}")
            return None
        return reference

    def _evidence_ids(self, selected, payloads, items, selected_ids, warnings):
        evidence = self._relation_candidates(
            items, "evidence", "source_entity_id", selected_ids, warnings
        )
        valid_ids = {item.canonical_id for item, _ in evidence}
        known_evidence = {
            item.canonical_id: item for item in items if item.entity_type == "evidence"
        }
        values = set(valid_ids)
        for item in selected:
            for value in payloads[item.canonical_id].get("evidence_ids", []):
                if isinstance(value, str) and value.strip():
                    clean, detections = sanitize_text(value, 241)
                    if detections or not clean or len(clean) > 240:
                        self._warn(warnings, "excluded_sensitive:evidence_id")
                    elif clean in valid_ids:
                        values.add(clean)
                    elif clean not in known_evidence:
                        self._warn(warnings, "invalid_relation:evidence_reference")
        ordered = sorted(values)
        if len(ordered) > MAX_EVIDENCE_IDS:
            self._warn(warnings, "limit_exceeded:evidence_ids")
        return ordered[:MAX_EVIDENCE_IDS]

    @staticmethod
    def _dedupe_label(values, attribute):
        result = []
        seen = set()
        for value in values:
            key = " ".join(getattr(value, attribute).casefold().split())
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def _main_text(self, module, screen, fields, controls, tables, states, events, warnings):
        lines = [f"Módulo: {module}", f"Pantalla: {screen}"]
        groups = (
            ("Campos", [x.label for x in fields]),
            ("Controles", [x.label for x in controls]),
            ("Estados", [x.title for x in states]),
            ("Eventos", [x.label for x in events]),
        )
        for name, values in groups:
            if values:
                lines.append(f"{name}: {'; '.join(dict.fromkeys(values))}")
        for table in tables:
            lines.append(f"Tabla: {table.name}")
            if table.columns:
                lines.append(
                    f"Columnas: {'; '.join(dict.fromkeys(x.label for x in table.columns))}"
                )
        text = "\n".join(lines)
        if len(text) > MAX_MAIN_CONTENT_CHARS:
            self._warn(warnings, "limit_exceeded:main_content_text")
            kept = []
            for line in lines:
                candidate = "\n".join([*kept, line])
                if len(candidate) > MAX_MAIN_CONTENT_CHARS:
                    break
                kept.append(line)
            text = "\n".join(kept)
        return text

    @staticmethod
    def _route(value):
        if not isinstance(value, str) or not value.strip():
            raise UnsafeScreenRouteError("La ruta de pantalla no es segura")
        candidate = value.strip()
        parsed = urlsplit(candidate)
        if (
            parsed.scheme
            or parsed.netloc
            or not parsed.path.startswith("/")
            or len(parsed.path) > 1000
        ):
            raise UnsafeScreenRouteError("La ruta de pantalla no es segura")
        return parsed.path.rstrip("/") or "/"

    @staticmethod
    def _warn(warnings, warning):
        if warning not in warnings and len(warnings) < MAX_WARNINGS:
            warnings.append(warning)
