from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StructuralOwner:
    scope_type: str
    scope_id: str


class StructuralOwnershipResolver:
    """Resolve a canonical structural item to the narrowest governed review scope."""

    _SCREEN_TYPES = {"ui_state", "field", "control", "table", "link", "event"}

    def owner_for_item(
        self,
        item: Any,
        items: Mapping[tuple[str, str], Any],
        *,
        _seen: set[tuple[str, str]] | None = None,
    ) -> StructuralOwner | None:
        if item is None:
            return None
        entity_type = str(getattr(item, "entity_type", "") or "")
        canonical_id = str(getattr(item, "canonical_id", "") or "")
        marker = (entity_type, canonical_id or f"@{id(item)}")
        seen = set() if _seen is None else _seen
        if marker in seen:
            return None
        seen.add(marker)
        payload = dict(getattr(item, "source_payload", {}) or {})

        if entity_type == "screen" and canonical_id:
            return StructuralOwner("screen", canonical_id)
        if entity_type == "module" and canonical_id:
            return StructuralOwner("module", canonical_id)
        if entity_type == "erp_system" and canonical_id:
            return StructuralOwner("system", canonical_id)
        if entity_type in self._SCREEN_TYPES:
            return self._screen_owner(payload.get("screen_id"), items)
        if entity_type == "table_column":
            table = items.get(("table", str(payload.get("table_id") or "")))
            return self.owner_for_item(table, items, _seen=seen)
        if entity_type == "transition":
            source = items.get(("ui_state", str(payload.get("source_state_id") or "")))
            target = items.get(("ui_state", str(payload.get("target_state_id") or "")))
            source_owner = self.owner_for_item(source, items, _seen=set(seen))
            target_owner = self.owner_for_item(target, items, _seen=set(seen))
            if (
                source_owner is not None
                and source_owner == target_owner
                and source_owner.scope_type == "screen"
            ):
                return source_owner
            return None
        if entity_type == "evidence":
            source_type = payload.get("source_entity_type")
            source_id = str(payload.get("source_entity_id") or "")
            if not isinstance(source_type, str) or not source_type or not source_id:
                return None
            source = items.get((source_type, source_id))
            return self.owner_for_item(source, items, _seen=seen)
        return None

    @staticmethod
    def _screen_owner(screen_id: Any, items: Mapping[tuple[str, str], Any]):
        if not isinstance(screen_id, str) or not screen_id:
            return None
        if ("screen", screen_id) not in items:
            return None
        return StructuralOwner("screen", screen_id)
