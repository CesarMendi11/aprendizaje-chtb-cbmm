from __future__ import annotations

from collections import Counter
from typing import Iterable

from .enums import IssueSeverity
from .models import CanonicalKnowledgeBase, ValidationIssue
from .privacy import contains_sensitive

SUPPORTED_SCHEMA_VERSIONS = {"1.0.0"}


class CanonicalKnowledgeValidator:
    def validate(self, knowledge: CanonicalKnowledgeBase) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if knowledge.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            issues.append(self._issue("error", "unsupported_schema", "Versión de esquema no soportada"))

        collections = {
            "module": knowledge.modules,
            "screen": knowledge.screens,
            "ui_state": knowledge.ui_states,
            "field": knowledge.fields,
            "control": knowledge.controls,
            "table": knowledge.tables,
            "table_column": knowledge.table_columns,
            "link": knowledge.links,
            "event": knowledge.events,
            "transition": knowledge.transitions,
            "evidence": knowledge.evidence,
        }
        all_ids = [knowledge.erp_system.id]
        for kind, entities in collections.items():
            ids = [entity.id for entity in entities]
            all_ids.extend(ids)
            for duplicate in _duplicates(ids):
                issues.append(self._issue("error", "duplicate_id", f"ID duplicado: {duplicate}", kind, duplicate))
        for duplicate in _duplicates(all_ids):
            issues.append(self._issue("error", "global_duplicate_id", f"ID no único: {duplicate}", entity_id=duplicate))

        screen_ids = {item.id for item in knowledge.screens}
        module_ids = {item.id for item in knowledge.modules}
        state_ids = {item.id for item in knowledge.ui_states}
        table_ids = {item.id for item in knowledge.tables}
        event_ids = {item.id for item in knowledge.events}
        evidence_ids = {item.id for item in knowledge.evidence}
        routes = [(item.erp_id, item.route) for item in knowledge.screens]
        for duplicate in _duplicates(routes):
            issues.append(self._issue("error", "duplicate_route", f"Ruta duplicada: {duplicate[1]}"))

        for module in knowledge.modules:
            self._ref(issues, module.erp_id, {knowledge.erp_system.id}, "module.erp_id", module.id)
        for screen in knowledge.screens:
            self._ref(issues, screen.erp_id, {knowledge.erp_system.id}, "screen.erp_id", screen.id)
            if screen.module_id:
                self._ref(issues, screen.module_id, module_ids, "screen.module_id", screen.id)
        for kind in (knowledge.fields, knowledge.controls, knowledge.tables, knowledge.links, knowledge.events):
            for entity in kind:
                self._ref(issues, entity.screen_id, screen_ids, "screen_id", entity.id)
        for state in knowledge.ui_states:
            self._ref(issues, state.screen_id, screen_ids, "ui_state.screen_id", state.id)
        for column in knowledge.table_columns:
            self._ref(issues, column.table_id, table_ids, "table_column.table_id", column.id)
        for transition in knowledge.transitions:
            self._ref(issues, transition.source_state_id, state_ids, "transition.source_state_id", transition.id)
            self._ref(issues, transition.target_state_id, state_ids, "transition.target_state_id", transition.id)
            if transition.event_id:
                self._ref(issues, transition.event_id, event_ids, "transition.event_id", transition.id)
        for kind, entities in collections.items():
            for entity in entities:
                for evidence_id in getattr(entity, "evidence_ids", []):
                    self._ref(issues, evidence_id, evidence_ids, f"{kind}.evidence_ids", entity.id)
        for screen in knowledge.screens:
            screen_text = (screen.title, screen.document_title, screen.main_content_text, screen.description)
            if any(contains_sensitive(value) for value in screen_text if value):
                issues.append(self._issue("error", "sensitive_content", "Contenido sensible en pantalla", "screen", screen.id))
        for evidence in knowledge.evidence:
            if contains_sensitive(evidence.observed_text):
                issues.append(self._issue("error", "sensitive_content", "Contenido sensible en evidencia", "evidence", evidence.id))
        return issues

    def errors(self, knowledge: CanonicalKnowledgeBase) -> list[ValidationIssue]:
        return [item for item in self.validate(knowledge) if item.severity == IssueSeverity.ERROR]

    def _ref(self, issues, value, valid, field, entity_id):
        if value not in valid:
            issues.append(self._issue("error", "unresolved_reference", f"Referencia inválida en {field}", entity_id=entity_id))

    @staticmethod
    def _issue(severity, code, message, entity_type=None, entity_id=None):
        return ValidationIssue(severity=severity, code=code, message=message, entity_type=entity_type, entity_id=entity_id)


def _duplicates(values: Iterable) -> list:
    return [item for item, count in Counter(values).items() if count > 1]
