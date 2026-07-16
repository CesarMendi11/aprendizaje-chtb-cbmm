from __future__ import annotations

import json
from pathlib import Path

from .models import CanonicalKnowledgeBase
from .validator import CanonicalKnowledgeValidator


class CanonicalKnowledgeRepository:
    def __init__(self, knowledge_path: Path | str):
        self.path = Path(knowledge_path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.knowledge = CanonicalKnowledgeBase.model_validate(payload)
        errors = CanonicalKnowledgeValidator().errors(self.knowledge)
        if errors:
            raise ValueError(f"Conocimiento canónico inválido: {len(errors)} errores")
        self._screens = {item.id: item for item in self.knowledge.screens}
        self._routes = {(item.erp_id, item.route): item for item in self.knowledge.screens}
        self._modules = {item.id: item for item in self.knowledge.modules}
        self._evidence = {item.id: item for item in self.knowledge.evidence}

    @property
    def counts(self): return dict(self.knowledge.statistics)
    def get_screen(self, screen_id): return self._screens.get(screen_id)
    def get_screen_by_route(self, route, erp_id=None):
        erp_id = erp_id or self.knowledge.erp_system.id
        route = route.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
        return self._routes.get((erp_id, route))
    def get_module(self, module_id): return self._modules.get(module_id)
    def get_module_screens(self, module_id): return tuple(item for item in self.knowledge.screens if item.module_id == module_id)
    def get_fields(self, screen_id): return tuple(item for item in self.knowledge.fields if item.screen_id == screen_id)
    def get_controls(self, screen_id): return tuple(item for item in self.knowledge.controls if item.screen_id == screen_id)
    def get_tables(self, screen_id): return tuple(item for item in self.knowledge.tables if item.screen_id == screen_id)
    def get_states(self, screen_id): return tuple(item for item in self.knowledge.ui_states if item.screen_id == screen_id)
    def get_transitions(self, state_id=None):
        return tuple(item for item in self.knowledge.transitions if state_id is None or item.source_state_id == state_id or item.target_state_id == state_id)
    def get_evidence(self, evidence_id): return self._evidence.get(evidence_id)
