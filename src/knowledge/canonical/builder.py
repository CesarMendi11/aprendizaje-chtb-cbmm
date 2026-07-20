from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .enums import ControlType, EvidenceType
from .ids import content_hash, normalize_route, normalize_text, stable_id
from .models import (
    BuildWarning, CanonicalKnowledgeBase, Control, ERPSystem, Event, Evidence,
    FieldEntity, Link, Module, Screen, Table, TableColumn, Transition, UIState,
)
from .privacy import SENSITIVE_REGIONS, build_safe_structural_text, safe_metadata, sanitize_text
from .validator import CanonicalKnowledgeValidator

SCHEMA_VERSION = "1.0.0"
GENERATOR_VERSION = "3A.2"
ARTIFACT_NAMES = (
    "screen_index.json", "routes_graph.json", "state_registry.json",
    "state_flow_graph.json", "event_policy_audit.json", "ui_event_execution_audit.json",
)


class ArtifactLoadError(ValueError):
    pass


class CanonicalKnowledgeBuilder:
    def __init__(self, project_root: Path | str = "."):
        self.root = Path(project_root).resolve()
        self.warnings: list[BuildWarning] = []
        self.omitted: dict[str, int] = {}
        self.sensitive_exclusions = 0

    def build_from_paths(self, profile_path: Path | str, structural_dir: Path | str | None = None):
        profile_path = self._resolve(profile_path)
        profile = self._load_yaml(profile_path)
        output = profile.get("output", {})
        source_dir = self._resolve(structural_dir or output.get("processed_structural_dir", "data/processed/structural"))
        artifacts = {name: self._load_json(source_dir / name, required=name == "screen_index.json") for name in ARTIFACT_NAMES}
        return self.build(profile, artifacts, source_profile=self._relative(profile_path), artifact_dir=source_dir)

    def build(self, profile: dict[str, Any], artifacts: dict[str, Any], *, source_profile="fixture", artifact_dir: Path | None = None):
        self.warnings = []
        self.omitted = {}
        self.sensitive_exclusions = 0
        erp_data = profile.get("erp", {})
        slug = normalize_text(erp_data.get("code") or erp_data.get("name") or "erp").replace(" ", "-")
        erp_id = stable_id("erp", slug)
        erp = ERPSystem(id=erp_id, slug=slug, name=str(erp_data.get("name") or slug), profile_name=Path(source_profile).stem, base_url=erp_data.get("base_url"), adapter=erp_data.get("adapter"), metadata=safe_metadata(erp_data.get("metadata")))
        hashes = self._artifact_hashes(artifacts, artifact_dir)
        source_refs = [name for name, payload in artifacts.items() if payload is not None]
        evidence: list[Evidence] = []

        screen_payloads = self._list(artifacts.get("screen_index.json"), "screens")
        route_graph = artifacts.get("routes_graph.json") or {}
        state_payloads = self._list(artifacts.get("state_registry.json"), "states") or self._list(artifacts.get("state_flow_graph.json"), "states")
        transition_payloads = self._list(artifacts.get("state_flow_graph.json"), "transitions")
        audit_screens = self._list(artifacts.get("event_policy_audit.json"), "screens")

        modules, route_modules = self._modules(erp_id, route_graph, transition_payloads, audit_screens, hashes, evidence)
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
            if module_id is None:
                self._warn("route_without_module", "Pantalla sin módulo inferible", "screen", screen_id)
            evidence_id = self._evidence(evidence, "screen", screen_id, "screen_index.json", hashes, EvidenceType.STRUCTURAL_JSON)
            title = str(raw.get("functional_title") or raw.get("title") or route)
            text, exclusions = build_safe_structural_text(title, self._structural_labels(raw))
            self.sensitive_exclusions += exclusions
            screen = Screen(id=screen_id, erp_id=erp_id, module_id=module_id, title=title, normalized_title=normalize_text(title), route=route, document_title=self._clean_optional(raw.get("document_title")), title_source=self._clean_optional(raw.get("title_source")), main_content_text=text, source_refs=["screen_index.json"], evidence_ids=[evidence_id], metadata=safe_metadata({"status": raw.get("status"), "knowledge_origin": raw.get("knowledge_origin")}))
            screens.append(screen)
            by_route[route] = screen

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
                if self._excluded(item): continue
                label = self._label(item) or str(item.get("name") or item.get("placeholder") or "field")
                fields.append(FieldEntity(id=stable_id("field", screen.id, normalize_text(label), item.get("name"), pos), screen_id=screen.id, label=label, normalized_label=normalize_text(label), name=self._clean_optional(item.get("name")), input_type=self._clean_optional(item.get("type") or item.get("input_type")), placeholder=self._safe_optional(item.get("placeholder")), required=bool(item.get("required")), readonly=bool(item.get("readonly")), disabled=bool(item.get("disabled")), region=item.get("region") or "main_content", selector=self._clean_optional(item.get("selector")), source_refs=["screen_index.json"], evidence_ids=ev_ids))
            for pos, item in enumerate(raw.get("buttons") or []):
                if self._excluded(item): continue
                label = self._label(item) or "unlabeled control"
                controls.append(Control(id=stable_id("control", screen.id, "button", normalize_text(label), pos), screen_id=screen.id, label=label, normalized_label=normalize_text(label), control_type=ControlType.BUTTON, mutative=self._mutative(item), region=item.get("region") or "main_content", selector=self._clean_optional(item.get("selector")), source_refs=["screen_index.json"], evidence_ids=ev_ids))
            for pos, item in enumerate(raw.get("tables") or []):
                if self._excluded(item): continue
                name = self._label(item) or None
                table_id = stable_id("table", screen.id, normalize_text(name), pos)
                table_columns = []
                for col_pos, header in enumerate(item.get("headers") or item.get("columns") or []):
                    header_name = str(header.get("name") if isinstance(header, dict) else header).strip()
                    if not header_name: continue
                    column = TableColumn(id=stable_id("table_column", table_id, normalize_text(header_name), col_pos), table_id=table_id, name=header_name, normalized_name=normalize_text(header_name), position=col_pos, source_refs=["screen_index.json"])
                    columns.append(column); table_columns.append(column.id)
                row_count = item.get("row_count") if isinstance(item.get("row_count"), int) else item.get("row_count_observed")
                tables.append(Table(id=table_id, screen_id=screen.id, name=name, normalized_name=normalize_text(name) or None, region=item.get("region") or "main_content", column_ids=table_columns, row_count_observed=row_count if isinstance(row_count, int) else None, source_refs=["screen_index.json"], evidence_ids=ev_ids))
            seen_links: set[tuple[str, str]] = set()
            for item in [*(raw.get("local_links") or []), *(raw.get("links") or [])]:
                if self._excluded(item): continue
                target = item.get("href") or item.get("target_route")
                if not isinstance(target, str) or not target.startswith("/"): continue
                label = self._label(item) or target
                key = (normalize_text(label), normalize_route(target))
                if key in seen_links: continue
                seen_links.add(key)
                target_route = normalize_route(target)
                links.append(Link(id=stable_id("link", screen.id, *key), screen_id=screen.id, label=label, normalized_label=key[0], target_route=target_route, target_screen_id=by_route.get(target_route).id if target_route in by_route else None, region=item.get("region") or "main_content", source_refs=["screen_index.json"], evidence_ids=ev_ids))

        states: list[UIState] = []
        state_map: dict[str, UIState] = {}
        for raw in state_payloads:
            route = normalize_route(raw.get("route"))
            screen = by_route.get(route)
            if not screen:
                self._warn("state_without_screen", "Estado omitido: ruta sin pantalla", "ui_state"); self._omit("ui_states"); continue
            original = str(raw.get("state_id") or raw.get("structural_signature") or "")
            structural = str(raw.get("structural_signature") or raw.get("structural_fingerprint") or original)
            path = raw.get("path") or {}
            depth = int((raw.get("metadata") or {}).get("depth", path.get("depth", 0)) or 0)
            state = UIState(id=stable_id("ui_state", screen.id, structural), screen_id=screen.id, route=route, depth=depth, title=str(raw.get("title") or screen.title), exact_fingerprint=self._clean_optional(raw.get("exact_signature")), structural_fingerprint=structural, is_route_root=depth == 0 or (raw.get("metadata") or {}).get("kind") == "route_root_state", observed_path=path.get("steps") or [], restore_path=path.get("steps") or [], source_refs=["state_registry.json"], evidence_ids=[])
            states.append(state); state_map[original] = state

        events: list[Event] = []
        transitions: list[Transition] = []
        for raw in transition_payloads:
            source = state_map.get(str(raw.get("source_state_id"))); target = state_map.get(str(raw.get("target_state_id")))
            if not source or not target:
                self._warn("incomplete_transition", "Transición omitida por estado no resuelto", "transition"); self._omit("transitions"); continue
            event_raw = raw.get("event") or {}
            category = str(event_raw.get("event_type") or event_raw.get("event_category") or "unknown")
            label = str(event_raw.get("label") or category)
            event_id = stable_id("event", source.id, category, normalize_text(label), event_raw.get("selector"))
            if event_id not in {item.id for item in events}:
                region = (event_raw.get("metadata") or {}).get("region") or event_raw.get("region") or "unknown"
                events.append(Event(id=event_id, screen_id=source.screen_id, source_state_id=source.id, label=label, normalized_label=normalize_text(label), category=category, policy_decision=str(event_raw.get("decision") or "unknown"), mutative=category == "mutative_action", selector=self._clean_optional(event_raw.get("selector")), region=region, source_refs=["state_flow_graph.json"], evidence_ids=[]))
            metadata = raw.get("metadata") or {}
            route_changed = bool(raw.get("changed_route", raw.get("route_changed", source.route != target.route)))
            transitions.append(Transition(id=stable_id("transition", source.id, event_id, target.id), source_state_id=source.id, target_state_id=target.id, event_id=event_id, category=category, changed=source.id != target.id, route_changed=route_changed, restore_strategy=metadata.get("restore_strategy"), depth=target.depth, observed=bool(raw.get("observed", True)), source_refs=["state_flow_graph.json"], evidence_ids=[]))

        entity_lists = {"modules": modules, "screens": screens, "ui_states": states, "fields": fields, "controls": controls, "tables": tables, "table_columns": columns, "links": links, "events": events, "transitions": transitions, "evidence": evidence}
        stats = {name: len(items) for name, items in entity_lists.items()}
        functional = {"erp_system": erp.model_dump(mode="json"), **{key: [item.model_dump(mode="json") for item in value] for key, value in entity_lists.items() if key != "evidence"}}
        version = content_hash(functional)[:16]
        knowledge = CanonicalKnowledgeBase(schema_version=SCHEMA_VERSION, knowledge_version=version, generated_at=datetime.now(timezone.utc), generator_version=GENERATOR_VERSION, source_profile=source_profile, source_artifacts=source_refs, source_artifact_hashes=hashes, erp_system=erp, build_warnings=self.warnings, statistics=stats, **entity_lists)
        errors = CanonicalKnowledgeValidator().errors(knowledge)
        if errors:
            raise ArtifactLoadError("Modelo canónico inválido: " + "; ".join(item.code for item in errors))
        return knowledge

    def build_report(self, knowledge, issues=()):
        return {"schema_version": knowledge.schema_version, "knowledge_version": knowledge.knowledge_version, "warnings": [item.model_dump(mode="json") for item in knowledge.build_warnings], "errors": [item.model_dump(mode="json") for item in issues if item.severity == "error"], "unresolved_references": sum(item.code == "unresolved_reference" for item in issues), "duplicates": sum("duplicate" in item.code for item in issues), "routes_without_module": sum(item.code == "route_without_module" for item in knowledge.build_warnings), "incomplete_transitions": sum(item.code == "incomplete_transition" for item in knowledge.build_warnings), "missing_evidence": 0, "sensitive_regions_excluded": self.sensitive_exclusions, "omitted_entities": self.omitted, "statistics": knowledge.statistics}

    def _modules(self, erp_id, graph, transitions, audit_screens, hashes, evidence):
        candidates: dict[str, set[str]] = {}
        state_modules: dict[str, str] = {}
        for edge in self._list(graph, "edges"):
            meta = edge.get("metadata") or {}
            if meta.get("event_category") == "expand_menu" and edge.get("label"):
                name = str(edge["label"])
                candidates.setdefault(name, set())
                if edge.get("target"):
                    state_modules[str(edge["target"])] = name
        for edge in self._list(graph, "edges"):
            name = state_modules.get(str(edge.get("source")))
            target = edge.get("target")
            if name and isinstance(target, str) and "#state:" not in target:
                candidates[name].add(normalize_route(target))
        for screen in audit_screens:
            for item in ((screen.get("pipeline") or {}).get("selected_for_exploration") or []):
                if item.get("event_category") == "expand_menu" and item.get("label"):
                    candidates.setdefault(str(item["label"]), set())
        nodes = self._list(graph, "nodes")
        for node in nodes:
            module = node.get("source_module")
            route = node.get("route")
            # Some crawlers store a source route/state in source_module. It is
            # provenance, not a functional module label.
            if module and module != "root" and route and not str(module).startswith("/"):
                candidates.setdefault(str(module), set()).add(normalize_route(route))
        modules=[]; route_modules={}
        for name in sorted(candidates, key=normalize_text):
            module_id=stable_id("module", erp_id, normalize_text(name))
            routes=sorted(candidates[name])
            prefix=self._common_prefix(routes) if routes else None
            ev=self._evidence(evidence, "module", module_id, "routes_graph.json", hashes, EvidenceType.STRUCTURAL_JSON)
            modules.append(Module(id=module_id, erp_id=erp_id, name=name, normalized_name=normalize_text(name), route_prefix=prefix, source_refs=["routes_graph.json"], evidence_ids=[ev]))
            for route in routes: route_modules[route]=module_id
        return modules, route_modules

    def _module_for_route(self, route, mappings):
        if route in mappings: return mappings[route]
        matches=[(prefix, mid) for prefix, mid in mappings.items() if route.startswith(prefix.rstrip("/")+"/")]
        return max(matches, default=(None, None), key=lambda item:len(item[0]))[1]

    def _evidence(self, evidence, entity_type, entity_id, artifact, hashes, kind):
        evidence_id=stable_id("evidence", entity_type, entity_id, artifact)
        evidence.append(Evidence(id=evidence_id, evidence_type=kind, artifact_path=f"data/processed/structural/{artifact}", artifact_hash=hashes.get(artifact), source_entity_type=entity_type, source_entity_id=entity_id))
        return evidence_id

    def _excluded(self, item):
        if str(item.get("region") or "").casefold() in SENSITIVE_REGIONS:
            self.sensitive_exclusions += 1; self._omit("sensitive_elements"); return True
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
        return next((str(item.get(key)).strip() for key in keys if item.get(key) and str(item.get(key)).strip()), "")

    @staticmethod
    def _label(item):
        return next((str(item.get(key)).strip() for key in ("label", "text", "aria_label", "title", "placeholder") if item.get(key) and str(item.get(key)).strip()), "")
    @staticmethod
    def _mutative(item): return str(item.get("type") or "").casefold() == "submit"
    @staticmethod
    def _list(payload, key): return payload.get(key, []) if isinstance(payload, dict) and isinstance(payload.get(key, []), list) else []
    @staticmethod
    def _clean_optional(value): return str(value).strip() if value is not None and str(value).strip() else None
    def _safe_optional(self, value):
        clean, count=sanitize_text(value, 300); self.sensitive_exclusions += count
        return clean or None
    def _warn(self, code, message, entity_type=None, entity_id=None): self.warnings.append(BuildWarning(code=code, message=message, entity_type=entity_type, entity_id=entity_id))
    def _omit(self, key): self.omitted[key]=self.omitted.get(key, 0)+1
    def _resolve(self, path):
        path=Path(path); return path if path.is_absolute() else self.root/path
    def _relative(self, path):
        try: return path.relative_to(self.root).as_posix()
        except ValueError: return path.as_posix()
    @staticmethod
    def _common_prefix(routes):
        if not routes: return None
        parts=[route.strip("/").split("/") for route in routes]; common=[]
        for values in zip(*parts):
            if len(set(values)) != 1: break
            common.append(values[0])
        return "/"+"/".join(common) if common else None
    def _load_json(self, path, required=False):
        if not path.exists():
            if required: raise ArtifactLoadError(f"Artefacto ausente: {self._relative(path)}")
            self._warn("missing_artifact", f"Artefacto opcional ausente: {path.name}"); return None
        try:
            payload=json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc: raise ArtifactLoadError(f"Artefacto corrupto: {path.name}") from exc
        if not isinstance(payload, dict): raise ArtifactLoadError(f"Artefacto inválido: {path.name}")
        return payload
    @staticmethod
    def _load_yaml(path):
        import yaml
        try: payload=yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc: raise ArtifactLoadError(f"Perfil inválido: {path}") from exc
        if not isinstance(payload, dict): raise ArtifactLoadError("El perfil no es un objeto")
        return payload
    def _artifact_hashes(self, artifacts, artifact_dir):
        result={}
        for name, payload in artifacts.items():
            if payload is None: continue
            path=artifact_dir/name if artifact_dir else None
            result[name]=hashlib.sha256(path.read_bytes()).hexdigest() if path and path.exists() else content_hash(payload)
        return result
