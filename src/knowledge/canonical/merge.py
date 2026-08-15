from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .ids import content_hash
from .models import BuildWarning, CanonicalKnowledgeBase
from .snapshot import CanonicalSnapshotContext
from .validator import CanonicalKnowledgeValidator


class CanonicalPartialMergeError(ValueError):
    """Raised when a partial canonical cannot safely replace its declared scope."""


@dataclass(frozen=True)
class CanonicalPartialMergeReport:
    scope: str
    target: str
    target_module_id: str | None
    target_screen_id: str | None
    base_knowledge_version: str
    partial_knowledge_version: str
    removed_counts: dict[str, int]
    inserted_counts: dict[str, int]
    preserved_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "target": self.target,
            "target_module_id": self.target_module_id,
            "target_screen_id": self.target_screen_id,
            "base_knowledge_version": self.base_knowledge_version,
            "partial_knowledge_version": self.partial_knowledge_version,
            "removed_counts": dict(self.removed_counts),
            "inserted_counts": dict(self.inserted_counts),
            "preserved_counts": dict(self.preserved_counts),
        }


_COLLECTIONS = (
    "modules",
    "screens",
    "ui_states",
    "fields",
    "controls",
    "tables",
    "table_columns",
    "links",
    "events",
    "transitions",
    "evidence",
)


class CanonicalPartialMerger:
    """Replace exactly one governed MODULE subtree or SCREEN inside a FULL snapshot."""

    GENERATOR_VERSION = "canonical-partial-merge-1.1.0"

    def merge(
        self,
        base: CanonicalKnowledgeBase,
        partial: CanonicalKnowledgeBase,
        snapshot: CanonicalSnapshotContext,
    ) -> tuple[CanonicalKnowledgeBase, CanonicalPartialMergeReport]:
        self._validate_inputs(base, partial, snapshot)
        base_scope = self._scope_for_snapshot(base, snapshot)
        partial_scope = self._scope_for_snapshot(partial, snapshot)
        partial_screen_override = self._screen_override(base, partial, snapshot)

        if snapshot.scope == "module":
            target_module_id = snapshot.target_module_id
            assert target_module_id is not None
            base_target = next(item for item in base.modules if item.id == target_module_id)
            partial_target = next(item for item in partial.modules if item.id == target_module_id)
            if (
                base_target.parent_module_id != partial_target.parent_module_id
                or base_target.depth != partial_target.depth
                or base_target.navigation_path != partial_target.navigation_path
            ):
                raise CanonicalPartialMergeError(
                    "El módulo raíz parcial cambió su ubicación jerárquica respecto de la base"
                )

        merged_collections: dict[str, list] = {}
        removed_counts: dict[str, int] = {}
        inserted_counts: dict[str, int] = {}
        preserved_counts: dict[str, int] = {}

        for name in _COLLECTIONS:
            base_items = list(getattr(base, name))
            partial_items = list(getattr(partial, name))
            removed_ids = base_scope[name]
            inserted_ids = partial_scope[name]

            preserved = [item for item in base_items if item.id not in removed_ids]
            inserted = [item for item in partial_items if item.id in inserted_ids]
            if name == "screens" and partial_screen_override is not None:
                inserted = [
                    partial_screen_override if item.id == partial_screen_override.id else item
                    for item in inserted
                ]

            preserved_ids = {item.id for item in preserved}
            collisions = sorted(preserved_ids & {item.id for item in inserted})
            if collisions:
                raise CanonicalPartialMergeError(
                    "El partial colisiona con entidades preservadas fuera del scope: "
                    f"{collisions[0]}"
                )

            merged_collections[name] = sorted([*preserved, *inserted], key=lambda item: item.id)
            removed_counts[name] = len(removed_ids)
            inserted_counts[name] = len(inserted)
            preserved_counts[name] = len(preserved)

        removed_entity_ids = set().union(
            *(base_scope[name] for name in _COLLECTIONS if name != "evidence")
        )
        partial_entity_ids = set().union(
            *(partial_scope[name] for name in _COLLECTIONS if name != "evidence")
        )
        warnings = self._merge_warnings(
            base.build_warnings,
            partial.build_warnings,
            removed_entity_ids,
            partial_entity_ids,
        )

        statistics = {name: len(merged_collections[name]) for name in _COLLECTIONS}
        source_artifacts, source_hashes = self._merge_provenance(base, partial)
        functional = {
            "erp_system": base.erp_system.model_dump(mode="json"),
            **{
                name: [item.model_dump(mode="json") for item in merged_collections[name]]
                for name in _COLLECTIONS
                if name != "evidence"
            },
        }
        knowledge_version = content_hash(functional)[:16]
        merged = CanonicalKnowledgeBase(
            schema_version=base.schema_version,
            knowledge_version=knowledge_version,
            generated_at=datetime.now(timezone.utc),
            generator_version=self.GENERATOR_VERSION,
            source_profile=base.source_profile,
            source_artifacts=source_artifacts,
            source_artifact_hashes=source_hashes,
            erp_system=base.erp_system,
            build_warnings=warnings,
            statistics=statistics,
            **merged_collections,
        )

        errors = CanonicalKnowledgeValidator().errors(merged)
        if errors:
            codes = ", ".join(sorted({item.code for item in errors}))
            raise CanonicalPartialMergeError(
                f"El resultado del merge canónico es inválido: {codes}"
            )

        assert snapshot.target is not None
        report = CanonicalPartialMergeReport(
            scope=snapshot.scope,
            target=snapshot.target,
            target_module_id=snapshot.target_module_id,
            target_screen_id=snapshot.target_screen_id,
            base_knowledge_version=base.knowledge_version,
            partial_knowledge_version=partial.knowledge_version,
            removed_counts=removed_counts,
            inserted_counts=inserted_counts,
            preserved_counts=preserved_counts,
        )
        return merged, report

    @staticmethod
    def _validate_inputs(
        base: CanonicalKnowledgeBase,
        partial: CanonicalKnowledgeBase,
        snapshot: CanonicalSnapshotContext,
    ) -> None:
        if snapshot.mode != "partial" or snapshot.scope not in {"module", "screen"}:
            raise CanonicalPartialMergeError(
                "El merger requiere un snapshot parcial de scope MODULE o SCREEN"
            )
        if base.schema_version != partial.schema_version:
            raise CanonicalPartialMergeError("Base y partial usan schema_version distintos")
        if base.erp_system.id != partial.erp_system.id:
            raise CanonicalPartialMergeError("Base y partial pertenecen a ERP distintos")
        if snapshot.erp_id != base.erp_system.id:
            raise CanonicalPartialMergeError("El snapshot parcial fija un ERP distinto de la base")
        if snapshot.base_knowledge_version != base.knowledge_version:
            raise CanonicalPartialMergeError(
                "El partial no fue construido sobre la knowledge_version base indicada"
            )

        if snapshot.scope == "module":
            target = snapshot.target_module_id
            if target not in {item.id for item in base.modules}:
                raise CanonicalPartialMergeError("El módulo objetivo no existe en la base FULL")
            if target not in {item.id for item in partial.modules}:
                raise CanonicalPartialMergeError(
                    "El módulo objetivo no existe en el partial MODULE"
                )
        else:
            target_screen_id = snapshot.target_screen_id
            base_screen = next(
                (item for item in base.screens if item.id == target_screen_id),
                None,
            )
            partial_screen = next(
                (item for item in partial.screens if item.id == target_screen_id),
                None,
            )
            if base_screen is None:
                raise CanonicalPartialMergeError("La pantalla objetivo no existe en la base FULL")
            if partial_screen is None:
                raise CanonicalPartialMergeError(
                    "La pantalla objetivo no existe en el partial SCREEN"
                )
            if base_screen.route != snapshot.target or partial_screen.route != snapshot.target:
                raise CanonicalPartialMergeError(
                    "La ruta de la pantalla objetivo no coincide con el scope SCREEN fijado"
                )

        for label, knowledge in (("base", base), ("partial", partial)):
            errors = CanonicalKnowledgeValidator().errors(knowledge)
            if errors:
                raise CanonicalPartialMergeError(
                    f"El canonical {label} es inválido antes del merge"
                )

    @staticmethod
    def _screen_override(
        base: CanonicalKnowledgeBase,
        partial: CanonicalKnowledgeBase,
        snapshot: CanonicalSnapshotContext,
    ):
        if snapshot.scope != "screen":
            return None
        target_screen_id = snapshot.target_screen_id
        base_screen = next(item for item in base.screens if item.id == target_screen_id)
        partial_screen = next(item for item in partial.screens if item.id == target_screen_id)
        # MODULE ownership is outside a SCREEN crawl boundary. Preserve it from ACTIVE;
        # a screen-only crawl may refresh screen content, not move the screen in hierarchy.
        return partial_screen.model_copy(update={"module_id": base_screen.module_id})

    @staticmethod
    def _module_subtree(knowledge: CanonicalKnowledgeBase, root_id: str) -> set[str]:
        children: dict[str, list[str]] = {}
        for module in knowledge.modules:
            if module.parent_module_id:
                children.setdefault(module.parent_module_id, []).append(module.id)

        result: set[str] = set()
        pending = [root_id]
        while pending:
            current = pending.pop()
            if current in result:
                raise CanonicalPartialMergeError(
                    "La jerarquía MODULE contiene un ciclo durante el merge"
                )
            result.add(current)
            pending.extend(children.get(current, ()))
        return result

    def _scope_for_snapshot(
        self,
        knowledge: CanonicalKnowledgeBase,
        snapshot: CanonicalSnapshotContext,
    ) -> dict[str, set[str]]:
        if snapshot.scope == "module":
            assert snapshot.target_module_id is not None
            module_ids = self._module_subtree(knowledge, snapshot.target_module_id)
            screen_ids = {
                item.id for item in knowledge.screens if item.module_id in module_ids
            }
            return self._entity_scope(knowledge, module_ids, screen_ids)

        assert snapshot.target_screen_id is not None
        screen_ids = {snapshot.target_screen_id}
        return self._entity_scope(knowledge, set(), screen_ids)

    def _entity_scope(
        self,
        knowledge: CanonicalKnowledgeBase,
        module_ids: set[str],
        screen_ids: set[str],
    ) -> dict[str, set[str]]:
        state_ids = {item.id for item in knowledge.ui_states if item.screen_id in screen_ids}
        field_ids = {item.id for item in knowledge.fields if item.screen_id in screen_ids}
        control_ids = {item.id for item in knowledge.controls if item.screen_id in screen_ids}
        table_ids = {item.id for item in knowledge.tables if item.screen_id in screen_ids}
        column_ids = {
            item.id for item in knowledge.table_columns if item.table_id in table_ids
        }
        link_ids = {item.id for item in knowledge.links if item.screen_id in screen_ids}
        event_ids = {item.id for item in knowledge.events if item.screen_id in screen_ids}
        transition_ids = {
            item.id
            for item in knowledge.transitions
            if item.source_state_id in state_ids
            or item.target_state_id in state_ids
            or (item.event_id is not None and item.event_id in event_ids)
        }
        owned_entity_ids = set().union(
            module_ids,
            screen_ids,
            state_ids,
            field_ids,
            control_ids,
            table_ids,
            column_ids,
            link_ids,
            event_ids,
            transition_ids,
        )
        referenced_evidence_ids = self._referenced_evidence_ids(
            knowledge,
            module_ids=module_ids,
            screen_ids=screen_ids,
            state_ids=state_ids,
            field_ids=field_ids,
            control_ids=control_ids,
            table_ids=table_ids,
            link_ids=link_ids,
            event_ids=event_ids,
            transition_ids=transition_ids,
        )
        evidence_ids = {
            item.id
            for item in knowledge.evidence
            if item.source_entity_id in owned_entity_ids
            or item.id in referenced_evidence_ids
        }
        return {
            "modules": module_ids,
            "screens": screen_ids,
            "ui_states": state_ids,
            "fields": field_ids,
            "controls": control_ids,
            "tables": table_ids,
            "table_columns": column_ids,
            "links": link_ids,
            "events": event_ids,
            "transitions": transition_ids,
            "evidence": evidence_ids,
        }

    @staticmethod
    def _referenced_evidence_ids(
        knowledge: CanonicalKnowledgeBase,
        **ids_by_collection: set[str],
    ) -> set[str]:
        result: set[str] = set()
        selections = {
            "modules": ids_by_collection["module_ids"],
            "screens": ids_by_collection["screen_ids"],
            "ui_states": ids_by_collection["state_ids"],
            "fields": ids_by_collection["field_ids"],
            "controls": ids_by_collection["control_ids"],
            "tables": ids_by_collection["table_ids"],
            "links": ids_by_collection["link_ids"],
            "events": ids_by_collection["event_ids"],
            "transitions": ids_by_collection["transition_ids"],
        }
        for name, selected in selections.items():
            for item in getattr(knowledge, name):
                if item.id in selected:
                    result.update(getattr(item, "evidence_ids", ()))
        return result

    @staticmethod
    def _merge_warnings(
        base: Iterable[BuildWarning],
        partial: Iterable[BuildWarning],
        removed_ids: set[str],
        partial_ids: set[str],
    ) -> list[BuildWarning]:
        kept = [item for item in base if not item.entity_id or item.entity_id not in removed_ids]
        inserted = [
            item for item in partial if not item.entity_id or item.entity_id in partial_ids
        ]
        unique: dict[tuple, BuildWarning] = {}
        for item in [*kept, *inserted]:
            key = (item.code, item.message, item.entity_type, item.entity_id)
            previous = unique.get(key)
            if previous is None or item.count > previous.count:
                unique[key] = item
        return sorted(
            unique.values(),
            key=lambda item: (
                item.code,
                item.entity_type or "",
                item.entity_id or "",
                item.message,
            ),
        )

    @staticmethod
    def _merge_provenance(
        base: CanonicalKnowledgeBase,
        partial: CanonicalKnowledgeBase,
    ) -> tuple[list[str], dict[str, str]]:
        artifacts = [
            *[f"base:{item}" for item in base.source_artifacts],
            *[f"partial:{item}" for item in partial.source_artifacts],
        ]
        hashes = {
            **{
                f"base:{key}": value
                for key, value in sorted(base.source_artifact_hashes.items())
            },
            **{
                f"partial:{key}": value
                for key, value in sorted(partial.source_artifact_hashes.items())
            },
        }
        return artifacts, hashes
