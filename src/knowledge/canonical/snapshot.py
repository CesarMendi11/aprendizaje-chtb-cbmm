from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class CanonicalSnapshotContext(BaseModel):
    """Provenance for one canonical artifact bundle.

    The canonical document contains the discovered knowledge itself; this context
    describes whether that document represents a full ERP snapshot or an isolated
    partial crawl that must be merged before it can become a full candidate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["full", "partial"]
    scope: Literal["full", "module", "screen"]
    target: str | None = None
    target_module_id: str | None = None
    target_screen_id: str | None = None
    base_knowledge_version_id: str | None = None
    base_knowledge_version: str | None = None
    erp_id: str | None = None

    @model_validator(mode="after")
    def validate_contract(self):
        target = (self.target or "").strip() or None
        module_id = (self.target_module_id or "").strip() or None
        screen_id = (self.target_screen_id or "").strip() or None
        base_id = (self.base_knowledge_version_id or "").strip() or None
        base_version = (self.base_knowledge_version or "").strip() or None
        erp_id = (self.erp_id or "").strip() or None

        if self.scope == "full":
            if self.mode != "full":
                raise ValueError("scope=full requiere mode=full")
            if any((target, module_id, screen_id, base_id, base_version)):
                raise ValueError("scope=full no acepta target ni versión base")
        elif self.scope == "module":
            if self.mode != "partial":
                raise ValueError("scope=module requiere mode=partial")
            if not module_id or not module_id.startswith("module:"):
                raise ValueError("scope=module requiere target_module_id canónico")
            if target != module_id:
                raise ValueError("scope=module requiere target igual a target_module_id")
            if screen_id:
                raise ValueError("scope=module no acepta target_screen_id")
            if not base_id or not base_version or not erp_id:
                raise ValueError("scope=module requiere versión base y erp_id fijados")
        else:
            if self.mode != "partial":
                raise ValueError("scope=screen requiere mode=partial")
            if not target or not target.startswith("/") or "://" in target:
                raise ValueError("scope=screen requiere una ruta interna")
            if module_id:
                raise ValueError("scope=screen no acepta target_module_id")
            if not screen_id or not screen_id.startswith("screen:"):
                raise ValueError("scope=screen requiere target_screen_id canónico")
            if not base_id or not base_version or not erp_id:
                raise ValueError("scope=screen requiere versión base y erp_id fijados")

        object.__setattr__(self, "target", target)
        object.__setattr__(self, "target_module_id", module_id)
        object.__setattr__(self, "target_screen_id", screen_id)
        object.__setattr__(self, "base_knowledge_version_id", base_id)
        object.__setattr__(self, "base_knowledge_version", base_version)
        object.__setattr__(self, "erp_id", erp_id)
        return self

    @classmethod
    def full(cls) -> "CanonicalSnapshotContext":
        return cls(mode="full", scope="full")
