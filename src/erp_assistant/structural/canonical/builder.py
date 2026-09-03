from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .enums import ControlType, EvidenceType
from .ids import content_hash, normalize_route, normalize_text, stable_id
from .models import (
    BuildWarning,
    CanonicalKnowledgeBase,
    Control,
    ERPSystem,
    Event,
    Evidence,
    FieldEntity,
    Link,
    Module,
    Screen,
    Table,
    TableColumn,
    Transition,
    UIState,
)
from .privacy import (
    SENSITIVE_REGIONS,
    build_safe_structural_text,
    safe_metadata,
    sanitize_text,
)
from .validator import CanonicalKnowledgeValidator

SCHEMA_VERSION = "1.1.0"
GENERATOR_VERSION = "4.0.6"
NONFUNCTIONAL_ICON_LABELS = {"bars-3"}
CONTROL_IDENTITY_LABEL_KEYS = ("label", "text", "aria_label", "title", "placeholder")
ARTIFACT_NAMES = (
    "screen_index.json",
    "routes_graph.json",
    "state_registry.json",
    "state_flow_graph.json",
    "event_policy_audit.json",
    "ui_event_execution_audit.json",
)


class ArtifactLoadError(ValueError):
    pass


class CanonicalKnowledgeBuilder:
    def __init__(self, project_root: Path | str = "."):
        self.root = Path(project_root).resolve()
        self.warnings: list[BuildWarning] = []
        self.omitted: dict[str, int] = {}
        self.sensitive_exclusions = 0

    def build_from_paths(
        self,
        profile_path: Path | str,
        structural_dir: Path | str | None = None,
        *,
        profile: dict[str, Any] | None = None,
        profile_sha256: str | None = None,
    ):
        profile_path = self._resolve(profile_path)
        if profile is None:
            profile, loaded_profile_sha256 = self._load_yaml(profile_path)
        else:
            if not profile_sha256:
                raise ArtifactLoadError(
                    "profile_sha256 es obligatorio cuando el perfil ya fue cargado"
                )
            loaded_profile_sha256 = profile_sha256
        output = profile.get("output", {})
        source_dir = self._resolve(
            structural_dir or output.get("processed_structural_dir", "data/processed/structural")
        )
        artifacts = {
            name: self._load_json(
                source_dir / name,
                required=name == "screen_index.json",
            )
            for name in ARTIFACT_NAMES
        }
        return self.build(
            profile,
            artifacts,
            source_profile=self._relative(profile_path),
            artifact_dir=source_dir,
            profile_sha256=loaded_profile_sha256,
        )

    def build(
        self,
        profile: dict[str, Any],
        artifacts: dict[str, Any],
        *,
        source_profile="fixture",
        artifact_dir: Path | None = None,
        profile_sha256: str | None = None,
    ):
        self.warnings = []
        self.omitted = {}
        self.sensitive_exclusions = 0
        erp_data = profile.get("erp", {})
        slug = normalize_text(erp_data.get("code") or erp_data.get("name") or "erp").replace(
            " ", "-"
        )
        erp_id = stable_id("erp", slug)
        erp = ERPSystem(
            id=erp_id,
            slug=slug,
            name=str(erp_data.get("name") or slug),
            profile_name=Path(source_profile).stem,
            base_url=erp_data.get("base_url"),
            adapter=erp_data.get("adapter"),
            metadata=safe_metadata(erp_data.get("metadata")),
        )
        hashes = self._artifact_hashes(artifacts, artifact_dir)
        artifact_base = (
            self._relative(artifact_dir) if artifact_dir else "data/processed/structural"
        )
        source_refs = [name for name, payload in artifacts.items() if payload is not None]
        if profile_sha256:
            profile_ref = f"profile:{source_profile}"
            hashes = {profile_ref: profile_sha256, **hashes}
            source_refs = [profile_ref, *source_refs]
        evidence: list[Evidence] = []

        screen_payloads = self._list(artifacts.get("screen_index.json"), "screens")
        route_graph = artifacts.get("routes_graph.json") or {}
        state_payloads = self._list(artifacts.get("state_registry.json"), "states") or self._list(
            artifacts.get("state_flow_graph.json"), "states"
        )
        transition_payloads = self._list(artifacts.get("state_flow_graph.json"), "transitions")

        home_route = normalize_route((profile.get("navigation") or {}).get("home_url") or "/")
        functional_routes = {
            normalize_route(item.get("route")) for item in screen_payloads if item.get("route")
        }
        state_screen_routes, ambiguous_state_owners = self._state_screen_routes(
            state_payloads,
            transition_payloads,
            functional_routes,
        )
        modules, route_modules = self._modules(
            erp_id,
            route_graph,
            hashes,
            evidence,
            home_route=home_route,
            functional_routes=functional_routes,
            artifact_base=artifact_base,
        )
        screens: list[Screen] = []
        by_route: dict[str, Screen] = {}
        for raw in screen_payloads:
            route = normalize_route(raw.get("route"))
            screen_id = stable_id("screen", erp_id, route)
            if raw.get("main_visible_text") or raw.get("visible_text"):
                # Count the excluded source, never inspect or report its values.
                self.sensitive_exclusions += 1
                self._omit("dynamic_text_sources")
            module_id = self._module_for_route(route, route_modules)
            if module_id is None and route != home_route:
                self._warn(
                    "route_without_module", "Pantalla sin módulo inferible", "screen", screen_id
                )
            evidence_id = self._evidence(
                evidence,
                "screen",
                screen_id,
                "screen_index.json",
                hashes,
                EvidenceType.STRUCTURAL_JSON,
                artifact_base,
            )
            title = str(raw.get("functional_title") or raw.get("title") or route)
            text, exclusions = build_safe_structural_text(title, self._structural_labels(raw))
            self.sensitive_exclusions += exclusions
            screen = Screen(
                id=screen_id,
                erp_id=erp_id,
                module_id=module_id,
                title=title,
                normalized_title=normalize_text(title),
                route=route,
                document_title=self._clean_optional(raw.get("document_title")),
                title_source=self._clean_optional(raw.get("title_source")),
                main_content_text=text,
                source_refs=["screen_index.json"],
                evidence_ids=[evidence_id],
                metadata=safe_metadata(
                    {"status": raw.get("status"), "knowledge_origin": raw.get("knowledge_origin")}
                ),
            )
            screens.append(screen)
            by_route[route] = screen

        states: list[UIState] = []
        state_map: dict[str, UIState] = {}
        state_raw_map: dict[str, dict[str, Any]] = {}
        screen_by_id = {screen.id: screen for screen in screens}
        for raw in state_payloads:
            original = str(raw.get("state_id") or raw.get("structural_signature") or "")
            route = normalize_route(raw.get("route"))
            owner_route = state_screen_routes.get(original)
            screen = by_route.get(owner_route) if owner_route else None
            if not screen:
                code = (
                    "ambiguous_state_screen_owner"
                    if original in ambiguous_state_owners
                    else "state_without_screen"
                )
                message = (
                    "Estado omitido: pantalla propietaria ambigua"
                    if code == "ambiguous_state_screen_owner"
                    else "Estado omitido: ruta sin pantalla propietaria"
                )
                self._warn(code, message, "ui_state")
                self._omit("ui_states")
                continue
            structural = str(
                raw.get("structural_signature") or raw.get("structural_fingerprint") or original
            )
            path = raw.get("path") or {}
            depth = int((raw.get("metadata") or {}).get("depth", path.get("depth", 0)) or 0)
            state_id = stable_id("ui_state", screen.id, structural)
            evidence_ids = []
            if route != screen.route:
                evidence_ids.append(
                    self._evidence(
                        evidence,
                        "ui_state",
                        state_id,
                        "state_registry.json",
                        hashes,
                        EvidenceType.STRUCTURAL_JSON,
                        artifact_base,
                    )
                )
            state = UIState(
                id=state_id,
                screen_id=screen.id,
                route=route,
                depth=depth,
                title=str(raw.get("title") or screen.title),
                exact_fingerprint=self._clean_optional(raw.get("exact_signature")),
                structural_fingerprint=structural,
                is_route_root=depth == 0
                or (raw.get("metadata") or {}).get("kind") == "route_root_state",
                observed_path=path.get("steps") or [],
                restore_path=path.get("steps") or [],
                source_refs=["state_registry.json"],
                evidence_ids=evidence_ids,
            )
            states.append(state)
            state_map[original] = state
            state_raw_map[original] = raw

        fields: list[FieldEntity] = []
        controls: list[Control] = []
        tables: list[Table] = []
        columns: list[TableColumn] = []
        links: list[Link] = []
        raw_by_route = {normalize_route(item.get("route")): item for item in screen_payloads}
        for screen in screens:
            raw = raw_by_route[screen.route]
            ev_ids = screen.evidence_ids
            for pos, item in enumerate(raw.get("inputs") or []):
                if self._excluded(item):
                    continue
                label = self._label(item) or str(
                    item.get("name") or item.get("placeholder") or "field"
                )
                fields.append(
                    FieldEntity(
                        id=stable_id(
                            "field", screen.id, normalize_text(label), item.get("name"), pos
                        ),
                        screen_id=screen.id,
                        label=label,
                        normalized_label=normalize_text(label),
                        name=self._clean_optional(item.get("name")),
                        input_type=self._clean_optional(item.get("type") or item.get("input_type")),
                        placeholder=self._safe_optional(item.get("placeholder")),
                        required=bool(item.get("required")),
                        readonly=bool(item.get("readonly")),
                        disabled=bool(item.get("disabled")),
                        region=item.get("region") or "main_content",
                        selector=self._clean_optional(item.get("selector")),
                        source_refs=["screen_index.json"],
                        evidence_ids=ev_ids,
                    )
                )
            for pos, item in enumerate(raw.get("buttons") or []):
                if self._excluded(item):
                    continue
                label = self._label(item) or "unlabeled control"
                identity_label = (
                    self._label_from_keys(item, CONTROL_IDENTITY_LABEL_KEYS)
                    or "unlabeled control"
                )
                controls.append(
                    Control(
                        id=stable_id(
                            "control",
                            screen.id,
                            "button",
                            normalize_text(identity_label),
                            pos,
                        ),
                        screen_id=screen.id,
                        label=label,
                        normalized_label=normalize_text(label),
                        control_type=ControlType.BUTTON,
                        mutative=self._mutative(item),
                        region=item.get("region") or "main_content",
                        selector=self._clean_optional(item.get("selector")),
                        source_refs=["screen_index.json"],
                        evidence_ids=ev_ids,
                    )
                )
            for pos, item in enumerate(raw.get("tables") or []):
                if self._excluded(item):
                    continue
                name = self._label(item) or None
                table_id = stable_id("table", screen.id, normalize_text(name), pos)
                table_columns = []
                for col_pos, header in enumerate(item.get("headers") or item.get("columns") or []):
                    header_name = str(
                        header.get("name") if isinstance(header, dict) else header
                    ).strip()
                    if not header_name:
                        continue
                    column = TableColumn(
                        id=stable_id(
                            "table_column", table_id, normalize_text(header_name), col_pos
                        ),
                        table_id=table_id,
                        name=header_name,
                        normalized_name=normalize_text(header_name),
                        position=col_pos,
                        source_refs=["screen_index.json"],
                    )
                    columns.append(column)
                    table_columns.append(column.id)
                row_count = (
                    item.get("row_count")
                    if isinstance(item.get("row_count"), int)
                    else item.get("row_count_observed")
                )
                tables.append(
                    Table(
                        id=table_id,
                        screen_id=screen.id,
                        name=name,
                        normalized_name=normalize_text(name) or None,
                        region=item.get("region") or "main_content",
                        column_ids=table_columns,
                        row_count_observed=row_count if isinstance(row_count, int) else None,
                        source_refs=["screen_index.json"],
                        evidence_ids=ev_ids,
                    )
                )
            seen_links: set[tuple[str, str]] = set()
            for item in [*(raw.get("local_links") or []), *(raw.get("links") or [])]:
                if self._excluded(item):
                    continue
                target = item.get("href") or item.get("target_route")
                if not isinstance(target, str) or not target.startswith("/"):
                    continue
                label = self._label(item) or target
                key = (normalize_text(label), normalize_route(target))
                if key in seen_links:
                    continue
                seen_links.add(key)
                target_route = normalize_route(target)
                links.append(
                    Link(
                        id=stable_id("link", screen.id, *key),
                        screen_id=screen.id,
                        label=label,
                        normalized_label=key[0],
                        target_route=target_route,
                        target_screen_id=by_route.get(target_route).id
                        if target_route in by_route
                        else None,
                        region=item.get("region") or "main_content",
                        source_refs=["screen_index.json"],
                        evidence_ids=ev_ids,
                    )
                )

        projected_states = sorted(
            (
                (state, state_raw_map[original])
                for original, state in state_map.items()
                if state.route != screen_by_id[state.screen_id].route
                and isinstance((state_raw_map[original]).get("summary"), dict)
            ),
            key=lambda pair: (
                pair[0].screen_id,
                pair[0].route,
                pair[0].depth,
                pair[0].structural_fingerprint,
            ),
        )
        for state, raw in projected_states:
            self._project_state_summary(
                screen=screen_by_id[state.screen_id],
                summary=raw["summary"],
                evidence_ids=state.evidence_ids,
                fields=fields,
                controls=controls,
                tables=tables,
                columns=columns,
                links=links,
                by_route=by_route,
            )

        events: list[Event] = []
        transitions: list[Transition] = []
        for raw in transition_payloads:
            source = state_map.get(str(raw.get("source_state_id")))
            target = state_map.get(str(raw.get("target_state_id")))
            if not source or not target:
                self._warn(
                    "incomplete_transition",
                    "Transición omitida por estado no resuelto",
                    "transition",
                )
                self._omit("transitions")
                continue
            event_raw = raw.get("event") or {}
            category = str(
                event_raw.get("event_type") or event_raw.get("event_category") or "unknown"
            )
            label = str(event_raw.get("label") or category)
            _, sensitive_detections = sanitize_text(label, 300)
            if sensitive_detections:
                self.sensitive_exclusions += sensitive_detections
                self._warn(
                    "sensitive_event_label_replaced",
                    "Etiqueta dinámica/sensible de Event reemplazada por su categoría estructural",
                    "event",
                )
                label = category
            event_id = stable_id(
                "event", source.id, category, normalize_text(label), event_raw.get("selector")
            )
            if event_id not in {item.id for item in events}:
                region = (
                    (event_raw.get("metadata") or {}).get("region")
                    or event_raw.get("region")
                    or "unknown"
                )
                events.append(
                    Event(
                        id=event_id,
                        screen_id=source.screen_id,
                        source_state_id=source.id,
                        label=label,
                        normalized_label=normalize_text(label),
                        category=category,
                        policy_decision=str(event_raw.get("decision") or "unknown"),
                        mutative=category == "mutative_action",
                        selector=self._clean_optional(event_raw.get("selector")),
                        region=region,
                        source_refs=["state_flow_graph.json"],
                        evidence_ids=[],
                    )
                )
            metadata = raw.get("metadata") or {}
            route_changed = bool(
                raw.get("changed_route", raw.get("route_changed", source.route != target.route))
            )
            # Un self-loop CONTENT_CHANGE sí produjo efecto observable: `changed`
            # debe reflejar el resultado del evento, no la igualdad de IDs.
            effect = str(
                metadata.get("effect")
                or ("ROUTE_CHANGE" if route_changed else None)
                or ("STRUCTURAL_CHANGE" if source.id != target.id else "CONTENT_CHANGE")
            )
            transitions.append(
                Transition(
                    id=stable_id("transition", source.id, event_id, target.id),
                    source_state_id=source.id,
                    target_state_id=target.id,
                    event_id=event_id,
                    category=category,
                    changed=effect != "NO_EFFECT",
                    effect=effect,
                    route_changed=route_changed,
                    restore_strategy=metadata.get("restore_strategy"),
                    depth=target.depth,
                    observed=bool(raw.get("observed", True)),
                    source_refs=["state_flow_graph.json"],
                    evidence_ids=[],
                )
            )

        entity_lists = {
            "modules": modules,
            "screens": screens,
            "ui_states": states,
            "fields": fields,
            "controls": controls,
            "tables": tables,
            "table_columns": columns,
            "links": links,
            "events": events,
            "transitions": transitions,
            "evidence": evidence,
        }
        stats = {name: len(items) for name, items in entity_lists.items()}
        functional = {
            "erp_system": erp.model_dump(mode="json"),
            **{
                key: [item.model_dump(mode="json") for item in value]
                for key, value in entity_lists.items()
                if key != "evidence"
            },
        }
        version = content_hash(functional)[:16]
        knowledge = CanonicalKnowledgeBase(
            schema_version=SCHEMA_VERSION,
            knowledge_version=version,
            generated_at=datetime.now(timezone.utc),
            generator_version=GENERATOR_VERSION,
            source_profile=source_profile,
            source_artifacts=source_refs,
            source_artifact_hashes=hashes,
            erp_system=erp,
            build_warnings=self.warnings,
            statistics=stats,
            **entity_lists,
        )
        errors = CanonicalKnowledgeValidator().errors(knowledge)
        if errors:
            raise ArtifactLoadError(
                "Modelo canónico inválido: " + "; ".join(item.code for item in errors)
            )
        return knowledge

    def build_report(self, knowledge, issues=()):
        return {
            "schema_version": knowledge.schema_version,
            "knowledge_version": knowledge.knowledge_version,
            "warnings": [item.model_dump(mode="json") for item in knowledge.build_warnings],
            "errors": [item.model_dump(mode="json") for item in issues if item.severity == "error"],
            "unresolved_references": sum(item.code == "unresolved_reference" for item in issues),
            "duplicates": sum("duplicate" in item.code for item in issues),
            "routes_without_module": sum(
                item.code == "route_without_module" for item in knowledge.build_warnings
            ),
            "incomplete_transitions": sum(
                item.code == "incomplete_transition" for item in knowledge.build_warnings
            ),
            "missing_evidence": 0,
            "sensitive_regions_excluded": self.sensitive_exclusions,
            "omitted_entities": self.omitted,
            "statistics": knowledge.statistics,
        }

    def _state_screen_routes(
        self,
        state_payloads,
        transition_payloads,
        functional_routes,
    ):
        state_routes = {}
        owners = {}
        root_refs = {}

        for raw in state_payloads:
            original = str(raw.get("state_id") or raw.get("structural_signature") or "")
            if not original:
                continue
            route = normalize_route(raw.get("route"))
            state_routes[original] = route
            if route in functional_routes:
                owners[original] = route
            path = raw.get("path") or {}
            root_ref = str(path.get("root_state_id") or "").strip()
            if root_ref:
                root_refs[original] = root_ref

        transition_edges = [
            (
                str(raw.get("source_state_id") or ""),
                str(raw.get("target_state_id") or ""),
            )
            for raw in transition_payloads
        ]

        ambiguous = set()
        while True:
            proposals = {}
            for state_id in state_routes:
                if state_id in owners:
                    continue
                candidates = set()
                root_ref = root_refs.get(state_id)
                if root_ref in owners:
                    candidates.add(owners[root_ref])
                for source_id, target_id in transition_edges:
                    if target_id == state_id and source_id in owners:
                        candidates.add(owners[source_id])
                if candidates:
                    proposals[state_id] = candidates

            changed = False
            for state_id in sorted(proposals):
                candidates = proposals[state_id]
                if len(candidates) == 1:
                    owners[state_id] = next(iter(candidates))
                    changed = True
                else:
                    ambiguous.add(state_id)

            if not changed:
                break

        return owners, ambiguous

    def _project_state_summary(
        self,
        *,
        screen,
        summary,
        evidence_ids,
        fields,
        controls,
        tables,
        columns,
        links,
        by_route,
    ):
        source_refs = ["state_registry.json"]

        field_keys = {
            (
                item.screen_id,
                item.normalized_label,
                item.name or "",
                item.input_type or "",
            )
            for item in fields
        }
        for item in summary.get("inputs") or []:
            if not isinstance(item, dict) or self._excluded(item):
                continue
            label = self._label(item) or str(
                item.get("name") or item.get("placeholder") or "field"
            )
            name = self._clean_optional(item.get("name"))
            input_type = self._clean_optional(item.get("type") or item.get("input_type"))
            key = (screen.id, normalize_text(label), name or "", input_type or "")
            if key in field_keys:
                continue
            field_keys.add(key)
            fields.append(
                FieldEntity(
                    id=stable_id("field", screen.id, "state", *key[1:]),
                    screen_id=screen.id,
                    label=label,
                    normalized_label=key[1],
                    name=name,
                    input_type=input_type,
                    placeholder=self._safe_optional(item.get("placeholder")),
                    required=bool(item.get("required")),
                    readonly=bool(item.get("readonly")),
                    disabled=bool(item.get("disabled")),
                    region=item.get("region") or "main_content",
                    selector=self._clean_optional(item.get("selector")),
                    source_refs=source_refs,
                    evidence_ids=evidence_ids,
                )
            )

        control_keys = {
            (item.screen_id, item.control_type.value, item.normalized_label, item.mutative)
            for item in controls
        }
        for item in summary.get("buttons") or []:
            if not isinstance(item, dict) or self._excluded(item):
                continue
            label = self._label(item) or "unlabeled control"
            mutative = self._mutative(item)
            key = (screen.id, ControlType.BUTTON.value, normalize_text(label), mutative)
            if key in control_keys:
                continue
            control_keys.add(key)
            controls.append(
                Control(
                    id=stable_id("control", screen.id, "state", "button", key[2], mutative),
                    screen_id=screen.id,
                    label=label,
                    normalized_label=key[2],
                    control_type=ControlType.BUTTON,
                    mutative=mutative,
                    region=item.get("region") or "main_content",
                    selector=self._clean_optional(item.get("selector")),
                    source_refs=source_refs,
                    evidence_ids=evidence_ids,
                )
            )

        table_keys = {}
        for item in tables:
            headers = tuple(
                column.normalized_name
                for column in columns
                if column.table_id == item.id
            )
            table_keys[(item.screen_id, headers, item.normalized_name or "")] = item.id

        for item in summary.get("tables") or []:
            if not isinstance(item, dict) or self._excluded(item):
                continue
            name = self._label(item) or None
            headers = []
            for header in item.get("headers") or item.get("columns") or []:
                header_name = str(
                    header.get("name") if isinstance(header, dict) else header
                ).strip()
                if header_name:
                    headers.append(header_name)
            normalized_headers = tuple(normalize_text(value) for value in headers)
            key = (screen.id, normalized_headers, normalize_text(name))
            if key in table_keys:
                continue
            table_id = stable_id(
                "table", screen.id, "state", key[2], normalized_headers
            )
            table_columns = []
            for col_pos, header_name in enumerate(headers):
                column = TableColumn(
                    id=stable_id(
                        "table_column", table_id, normalize_text(header_name), col_pos
                    ),
                    table_id=table_id,
                    name=header_name,
                    normalized_name=normalize_text(header_name),
                    position=col_pos,
                    source_refs=source_refs,
                )
                columns.append(column)
                table_columns.append(column.id)
            tables.append(
                Table(
                    id=table_id,
                    screen_id=screen.id,
                    name=name,
                    normalized_name=normalize_text(name) or None,
                    region=item.get("region") or "main_content",
                    column_ids=table_columns,
                    row_count_observed=None,
                    source_refs=source_refs,
                    evidence_ids=evidence_ids,
                )
            )
            table_keys[key] = table_id

        link_keys = {
            (item.screen_id, item.normalized_label, item.target_route)
            for item in links
        }
        for item in [*(summary.get("local_links") or []), *(summary.get("links") or [])]:
            if not isinstance(item, dict) or self._excluded(item):
                continue
            target = item.get("href") or item.get("target_route")
            if not isinstance(target, str) or not target.startswith("/"):
                continue
            target_route = normalize_route(target)
            label = self._label(item) or target_route
            key = (screen.id, normalize_text(label), target_route)
            if key in link_keys:
                continue
            link_keys.add(key)
            links.append(
                Link(
                    id=stable_id("link", screen.id, key[1], target_route),
                    screen_id=screen.id,
                    label=label,
                    normalized_label=key[1],
                    target_route=target_route,
                    target_screen_id=(
                        by_route[target_route].id if target_route in by_route else None
                    ),
                    region=item.get("region") or "main_content",
                    source_refs=source_refs,
                    evidence_ids=evidence_ids,
                )
            )

    def _modules(
        self,
        erp_id,
        graph,
        hashes,
        evidence,
        *,
        home_route="/",
        functional_routes=None,
        artifact_base="data/processed/structural",
    ):
        functional_routes = {normalize_route(route) for route in (functional_routes or set())}
        nodes = self._list(graph, "nodes")
        edges = self._list(graph, "edges")
        normalized_home = normalize_route(home_route)

        def state_path(node):
            metadata = (node or {}).get("metadata") or {}
            path = metadata.get("path") or {}
            return path if isinstance(path, dict) else {}

        def menu_steps(node):
            result = []

            for step in state_path(node).get("steps") or []:
                if not isinstance(step, dict):
                    continue

                event = step.get("event") or {}
                if not isinstance(event, dict):
                    continue

                category = str(event.get("event_type") or event.get("event_category") or "").strip()
                label = str(event.get("label") or "").strip()

                if category != "expand_menu" or not label:
                    continue

                result.append(
                    {
                        "name": label,
                        "selector": str(event.get("selector") or "").strip(),
                    }
                )

            return result

        def segment_origin(step):
            selector = str(step.get("selector") or "").strip()

            if selector:
                return selector

            name = " ".join(str(step.get("name") or "").strip().split())
            return f"label:{name.casefold()}"

        # UIState.path is the authoritative navigation hierarchy.
        # Every expand_menu prefix becomes a Module candidate.
        candidates_by_key = {}
        state_leaf_keys = {}

        for node in nodes:
            metadata = (node or {}).get("metadata") or {}

            if metadata.get("kind") != "ui_state":
                continue

            if normalize_route(metadata.get("base_route") or "/") != normalized_home:
                continue

            steps = menu_steps(node)

            if not steps:
                continue

            origins = tuple(segment_origin(step) for step in steps)
            names = [str(step["name"]).strip() for step in steps]

            # Create every hierarchy prefix:
            # A
            # A/B
            # A/B/C
            for index in range(len(steps)):
                key = origins[: index + 1]

                if key not in candidates_by_key:
                    candidates_by_key[key] = {
                        "key": key,
                        "parent_key": key[:-1] or None,
                        "name": names[index],
                        "navigation_path": names[: index + 1],
                        # ``origin_path`` is identity provenance and may use a
                        # label fallback when the extractor had no selector.
                        "origin_path": list(key),
                        # Replay provenance must never use that fallback: the
                        # MODULE crawler clicks these values as CSS selectors.
                        "selector_path": [
                            str(step.get("selector") or "").strip() for step in steps[: index + 1]
                        ],
                        "depth": index,
                        "direct_routes": set(),
                        "subtree_routes": set(),
                    }

            leaf_key = origins

            for state_key in (
                node.get("id"),
                node.get("route"),
            ):
                if state_key:
                    state_leaf_keys[str(state_key)] = leaf_key

        outgoing = {}

        for edge in edges:
            outgoing.setdefault(
                str(edge.get("source") or ""),
                [],
            ).append(edge)

        if candidates_by_key:
            # A route discovered from a navigation state belongs
            # to the most specific module represented by that state.
            for state_id, leaf_key in state_leaf_keys.items():
                candidate = candidates_by_key.get(leaf_key)

                if candidate is None:
                    continue

                for edge in outgoing.get(state_id, []):
                    target = edge.get("target")

                    if (
                        isinstance(target, str)
                        and target.startswith("/")
                        and "#state:" not in target
                    ):
                        candidate["direct_routes"].add(normalize_route(target))

        # A parent remains functional when a descendant contains
        # a functional screen.
        for candidate in candidates_by_key.values():
            candidate["subtree_routes"] = set(candidate["direct_routes"])

        for candidate in sorted(
            candidates_by_key.values(),
            key=lambda item: item["depth"],
            reverse=True,
        ):
            parent_key = candidate["parent_key"]

            if parent_key and parent_key in candidates_by_key:
                candidates_by_key[parent_key]["subtree_routes"].update(candidate["subtree_routes"])

        publishable = []

        for candidate in candidates_by_key.values():
            functional = candidate["subtree_routes"] & functional_routes

            if functional_routes and not functional:
                self._warn(
                    "module_without_functional_screen",
                    (f"Módulo omitido sin pantalla funcional observada: {candidate['name']}"),
                    "module",
                )
                self._omit("modules_without_functional_screen")
                continue

            publishable.append(candidate)

        normalized_groups = {}
        exact_groups = {}

        for candidate in publishable:
            normalized_path = tuple(normalize_text(name) for name in candidate["navigation_path"])
            exact_path = tuple(
                " ".join(name.strip().split()) for name in candidate["navigation_path"]
            )

            normalized_groups.setdefault(
                normalized_path,
                [],
            ).append(candidate)
            exact_groups.setdefault(
                exact_path,
                [],
            ).append(candidate)

        def candidate_sort_key(item):
            return (
                item["depth"],
                tuple(normalize_text(name) for name in item["navigation_path"]),
                tuple(item["navigation_path"]),
                tuple(item["origin_path"]),
            )

        # IDs are computed before entities so child modules can
        # reference their parent's stable canonical ID.
        id_by_key = {}

        for candidate in sorted(
            publishable,
            key=candidate_sort_key,
        ):
            normalized_path = tuple(normalize_text(name) for name in candidate["navigation_path"])
            exact_path = tuple(
                " ".join(name.strip().split()) for name in candidate["navigation_path"]
            )

            id_parts = [
                erp_id,
                normalized_path,
            ]

            if (
                len(
                    normalized_groups.get(
                        normalized_path,
                        [],
                    )
                )
                > 1
            ):
                discriminator = content_hash(exact_path)[:12]

                if (
                    len(
                        exact_groups.get(
                            exact_path,
                            [],
                        )
                    )
                    > 1
                ):
                    discriminator = content_hash(
                        {
                            "path": exact_path,
                            "origin_path": (candidate["origin_path"]),
                        }
                    )[:16]

                id_parts.append(discriminator)

            id_by_key[candidate["key"]] = stable_id(
                "module",
                *id_parts,
            )

        modules = []
        route_modules = {}
        route_depths = {}

        for candidate in sorted(
            publishable,
            key=candidate_sort_key,
        ):
            module_id = id_by_key[candidate["key"]]
            parent_key = candidate["parent_key"]
            parent_module_id = id_by_key.get(parent_key) if parent_key else None

            routes = sorted(candidate["subtree_routes"])
            prefix = self._common_prefix(routes) if routes else None

            ev = self._evidence(
                evidence,
                "module",
                module_id,
                "routes_graph.json",
                hashes,
                EvidenceType.STRUCTURAL_JSON,
                artifact_base,
            )

            modules.append(
                Module(
                    id=module_id,
                    erp_id=erp_id,
                    parent_module_id=(parent_module_id),
                    depth=candidate["depth"],
                    navigation_path=list(candidate["navigation_path"]),
                    name=candidate["name"],
                    normalized_name=normalize_text(candidate["name"]),
                    route_prefix=prefix,
                    source_refs=["routes_graph.json"],
                    evidence_ids=[ev],
                    metadata=safe_metadata(
                        (
                            {
                                "navigation_origin": candidate["selector_path"][-1],
                                "navigation_origin_path": " || ".join(candidate["selector_path"]),
                            }
                            if candidate["selector_path"] and all(candidate["selector_path"])
                            else {}
                        )
                    ),
                )
            )

            # Routes are owned by the most-specific module.
            for route in sorted(candidate["direct_routes"]):
                existing = route_modules.get(route)
                existing_depth = route_depths.get(
                    route,
                    -1,
                )

                if existing and existing != module_id:
                    if candidate["depth"] > existing_depth:
                        route_modules[route] = module_id
                        route_depths[route] = candidate["depth"]
                    elif candidate["depth"] == existing_depth:
                        self._warn(
                            "ambiguous_module_route",
                            (f"Ruta observada bajo más de un módulo: {route}"),
                            "module",
                            module_id,
                        )

                    continue

                route_modules[route] = module_id
                route_depths[route] = candidate["depth"]

        return modules, route_modules

    def _module_for_route(self, route, mappings):
        if route in mappings:
            return mappings[route]
        matches = [
            (prefix, mid)
            for prefix, mid in mappings.items()
            if route.startswith(prefix.rstrip("/") + "/")
        ]
        return max(matches, default=(None, None), key=lambda item: len(item[0]))[1]

    def _evidence(
        self,
        evidence,
        entity_type,
        entity_id,
        artifact,
        hashes,
        kind,
        artifact_base="data/processed/structural",
    ):
        evidence_id = stable_id("evidence", entity_type, entity_id, artifact)
        evidence.append(
            Evidence(
                id=evidence_id,
                evidence_type=kind,
                artifact_path=f"{artifact_base.rstrip('/')}/{artifact}",
                artifact_hash=hashes.get(artifact),
                source_entity_type=entity_type,
                source_entity_id=entity_id,
            )
        )
        return evidence_id

    def _excluded(self, item):
        if str(item.get("region") or "").casefold() in SENSITIVE_REGIONS:
            self.sensitive_exclusions += 1
            self._omit("sensitive_elements")
            return True
        return False

    @classmethod
    def _structural_labels(cls, screen):
        labels: list[str] = []
        for item in screen.get("inputs") or []:
            if isinstance(item, dict) and not cls._is_sensitive_region(item):
                label = cls._label_from_keys(item, ("label", "aria_label", "title"))
                if label:
                    labels.append(label)
                placeholder = item.get("placeholder")
                if placeholder:
                    labels.append(str(placeholder).strip())
        for item in screen.get("buttons") or []:
            if isinstance(item, dict) and not cls._is_sensitive_region(item):
                label = cls._label(item)
                if label:
                    labels.append(label)
        for table in screen.get("tables") or []:
            if not isinstance(table, dict) or cls._is_sensitive_region(table):
                continue
            name = cls._label_from_keys(table, ("label", "name", "title"))
            if name:
                labels.append(name)
            for header in table.get("headers") or table.get("columns") or []:
                value = header.get("name") if isinstance(header, dict) else header
                if value:
                    labels.append(str(value).strip())
        for item in [*(screen.get("local_links") or []), *(screen.get("links") or [])]:
            if isinstance(item, dict) and not cls._is_sensitive_region(item):
                label = cls._label(item)
                if label:
                    labels.append(label)
        return labels

    @staticmethod
    def _is_sensitive_region(item):
        return str(item.get("region") or "").casefold() in SENSITIVE_REGIONS

    @staticmethod
    def _label_from_keys(item, keys):
        return next(
            (
                str(item.get(key)).strip()
                for key in keys
                if item.get(key) and str(item.get(key)).strip()
            ),
            "",
        )

    @staticmethod
    def _label(item):
        for key in (
            "label",
            "text",
            "aria_label",
            "title",
            "placeholder",
            "icon_label",
        ):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            if key == "icon_label" and value.casefold() in NONFUNCTIONAL_ICON_LABELS:
                continue
            return value
        return ""

    @staticmethod
    def _mutative(item):
        return str(item.get("type") or "").casefold() == "submit"

    @staticmethod
    def _list(payload, key):
        return (
            payload.get(key, [])
            if isinstance(payload, dict) and isinstance(payload.get(key, []), list)
            else []
        )

    @staticmethod
    def _clean_optional(value):
        return str(value).strip() if value is not None and str(value).strip() else None

    def _safe_optional(self, value):
        clean, count = sanitize_text(value, 300)
        self.sensitive_exclusions += count
        return clean or None

    def _warn(self, code, message, entity_type=None, entity_id=None):
        self.warnings.append(
            BuildWarning(code=code, message=message, entity_type=entity_type, entity_id=entity_id)
        )

    def _omit(self, key):
        self.omitted[key] = self.omitted.get(key, 0) + 1

    def _resolve(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.root / path

    def _relative(self, path):
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _common_prefix(routes):
        if not routes:
            return None
        parts = [route.strip("/").split("/") for route in routes]
        common = []
        for values in zip(*parts, strict=False):
            if len(set(values)) != 1:
                break
            common.append(values[0])
        return "/" + "/".join(common) if common else None

    def _load_json(self, path, required=False):
        if not path.exists():
            if required:
                raise ArtifactLoadError(f"Artefacto ausente: {self._relative(path)}")
            self._warn("missing_artifact", f"Artefacto opcional ausente: {path.name}")
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ArtifactLoadError(f"Artefacto corrupto: {path.name}") from exc
        if not isinstance(payload, dict):
            raise ArtifactLoadError(f"Artefacto inválido: {path.name}")
        return payload

    @staticmethod
    def _load_yaml(path):
        import yaml

        try:
            raw = path.read_bytes()
            payload = yaml.safe_load(raw.decode("utf-8"))
        except Exception as exc:
            raise ArtifactLoadError(f"Perfil inválido: {path}") from exc
        if not isinstance(payload, dict):
            raise ArtifactLoadError("El perfil no es un objeto")
        return payload, hashlib.sha256(raw).hexdigest()

    def _artifact_hashes(self, artifacts, artifact_dir):
        result = {}
        for name, payload in artifacts.items():
            if payload is None:
                continue
            path = artifact_dir / name if artifact_dir else None
            result[name] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path and path.exists()
                else content_hash(payload)
            )
        return result
