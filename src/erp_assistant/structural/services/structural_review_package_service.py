from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import ReviewSource
from erp_assistant.persistence.postgres.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    ReviewAction,
)

from .structural_ownership import StructuralOwnershipResolver
from .version_diff_service import (
    VersionDiff,
    VersionDiffChangeType,
    VersionDiffItem,
    VersionDiffService,
)


class StructuralReviewPackageError(ValueError):
    pass


@dataclass(frozen=True)
class StructuralReviewChange:
    change_type: str
    entity_type: str
    canonical_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    removal_confirmation: str | None
    requires_removal_review: bool


@dataclass(frozen=True)
class StructuralScreenReviewPackage:
    screen_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    title: str | None
    route: str | None
    module_id: str | None
    module_path: tuple[str, ...]
    change_type: str
    active_review_status: str | None
    candidate_review_status: str | None
    carry_forward: bool | None
    counts: dict[str, int]
    unconfirmed_removals: int
    review_required: bool
    changes: tuple[StructuralReviewChange, ...]


@dataclass(frozen=True)
class StructuralReviewPackage:
    active_version_id: str
    active_knowledge_version: str
    candidate_version_id: str
    candidate_knowledge_version: str
    erp_id: str
    candidate_origin: str
    diff_totals: dict[str, int]
    affected_screens: int
    screens_with_changes: int
    screens_unchanged: int
    unconfirmed_removals: int
    unscoped_changes: tuple[StructuralReviewChange, ...]
    packages: tuple[StructuralScreenReviewPackage, ...]


class StructuralReviewPackageService:
    """Read-only, canonical-reference-only review grouping for a governed version diff."""

    def __init__(self, session: Session):
        self.session = session
        self.ownership = StructuralOwnershipResolver()

    def build(
        self,
        candidate_version_id: uuid.UUID | str,
        *,
        changed_only: bool = False,
        module_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> StructuralReviewPackage:
        diff = VersionDiffService(self.session).compare(candidate_version_id)
        candidate = self.session.get(KnowledgeVersionRecord, uuid.UUID(diff.candidate_version_id))
        active = self.session.get(KnowledgeVersionRecord, uuid.UUID(diff.active_version_id))
        if candidate is None or active is None or candidate.erp_id != active.erp_id:
            raise StructuralReviewPackageError("Candidate y ACTIVE no corresponden al mismo ERP.")
        raw_candidate = diff.candidate_origin in {
            "full_canonical",
            "partial_module_merge",
            "partial_screen_merge",
        }
        active_items = self._items(active.id)
        candidate_items = self._items(candidate.id)
        packages, unscoped = self._group(diff, active_items, candidate_items, raw_candidate)
        visible = [package for package in packages if not changed_only or package.review_required]
        if module_id is not None:
            visible = [package for package in visible if package.module_id == module_id]
        page = tuple(visible[offset:] if limit is None else visible[offset : offset + limit])
        changed_screens = sum(package.review_required for package in packages)
        return StructuralReviewPackage(
            active_version_id=diff.active_version_id,
            active_knowledge_version=diff.active_knowledge_version,
            candidate_version_id=diff.candidate_version_id,
            candidate_knowledge_version=diff.candidate_knowledge_version,
            erp_id=diff.erp_id,
            candidate_origin=diff.candidate_origin,
            diff_totals=diff.totals,
            affected_screens=len(packages),
            screens_with_changes=changed_screens,
            screens_unchanged=len(packages) - changed_screens,
            unconfirmed_removals=sum(
                change.requires_removal_review for package in packages for change in package.changes
            )
            + sum(change.requires_removal_review for change in unscoped),
            unscoped_changes=tuple(unscoped),
            packages=page,
        )

    def _items(self, version_id: uuid.UUID) -> dict[tuple[str, str], KnowledgeItem]:
        return {
            (item.entity_type, item.canonical_id): item
            for item in self.session.scalars(
                select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == version_id)
            )
        }

    def _group(self, diff: VersionDiff, active_items, candidate_items, raw_candidate):
        grouped: dict[str, list[VersionDiffItem]] = defaultdict(list)
        unscoped: list[VersionDiffItem] = []
        for item in diff.items:
            screen_id = self._screen_owner(item, active_items, candidate_items)
            (grouped[screen_id] if screen_id else unscoped).append(item)
        packages = [
            self._package(screen_id, entries, active_items, candidate_items, raw_candidate)
            for screen_id, entries in grouped.items()
        ]
        packages.sort(key=lambda value: value.screen_id)
        return packages, [
            self._change(item, raw_candidate)
            for item in sorted(unscoped, key=lambda value: (value.entity_type, value.canonical_id))
        ]

    def _screen_owner(self, diff_item, active_items, candidate_items):
        key = (diff_item.entity_type, diff_item.canonical_id)
        if diff_item.change_type == VersionDiffChangeType.NEW:
            item = candidate_items.get(key)
            return self._owner_for_item(item, candidate_items) if item else None
        if diff_item.change_type == VersionDiffChangeType.REMOVED:
            item = active_items.get(key)
            return self._owner_for_item(item, active_items) if item else None
        active = active_items.get(key)
        candidate = candidate_items.get(key)
        if active is None or candidate is None:
            return None
        active_owner = self._owner_for_item(active, active_items)
        candidate_owner = self._owner_for_item(candidate, candidate_items)
        return (
            active_owner if active_owner is not None and active_owner == candidate_owner else None
        )

    def _owner_for_item(self, item, items):
        owner = self.ownership.owner_for_item(item, items)
        return owner.scope_id if owner is not None and owner.scope_type == "screen" else None

    def _package(self, screen_id, entries, active_items, candidate_items, raw_candidate):
        screen = next((item for item in entries if item.entity_type == "screen"), None)
        active = active_items.get(("screen", screen_id))
        candidate = candidate_items.get(("screen", screen_id))
        source = candidate or active
        payload = dict(source.source_payload or {}) if source else {}
        counts = Counter(item.change_type.value for item in entries)
        changes = tuple(
            self._change(item, raw_candidate)
            for item in sorted(entries, key=lambda value: (value.entity_type, value.canonical_id))
        )
        removed = counts[VersionDiffChangeType.REMOVED.value]
        module_id = payload.get("module_id") if isinstance(payload.get("module_id"), str) else None
        return StructuralScreenReviewPackage(
            screen_id=screen_id,
            active_item_id=str(active.id) if active else None,
            candidate_item_id=str(candidate.id) if candidate else None,
            title=(candidate.title if candidate else active.title if active else None),
            route=(candidate.route if candidate else active.route if active else None),
            module_id=module_id,
            module_path=self._module_path(
                module_id, candidate_items if candidate else active_items
            ),
            change_type=screen.change_type.value if screen else "unchanged",
            active_review_status=str(active.current_review_status) if active else None,
            candidate_review_status=str(candidate.current_review_status) if candidate else None,
            carry_forward=self._carry_forward(screen, active, candidate),
            counts={kind.value: counts[kind.value] for kind in VersionDiffChangeType},
            unconfirmed_removals=removed if raw_candidate else 0,
            review_required=any(
                counts[kind.value]
                for kind in (
                    VersionDiffChangeType.NEW,
                    VersionDiffChangeType.MODIFIED,
                    VersionDiffChangeType.REMOVED,
                )
            ),
            changes=changes,
        )

    @staticmethod
    def _module_path(module_id, items):
        if not module_id:
            return ()
        path, seen, current = [], set(), module_id
        while current:
            if current in seen:
                raise StructuralReviewPackageError("La jerarquía de módulos contiene un ciclo.")
            seen.add(current)
            module = items.get(("module", current))
            if module is None:
                raise StructuralReviewPackageError(
                    "La pantalla referencia un módulo inexistente o un padre no resoluble."
                )
            path.append(current)
            parent = (module.source_payload or {}).get("parent_module_id")
            current = parent if isinstance(parent, str) else None
        return tuple(reversed(path))

    def _carry_forward(self, screen, active, candidate):
        if (
            screen is None
            or screen.change_type != VersionDiffChangeType.UNCHANGED
            or active is None
            or candidate is None
        ):
            return None
        action = self.session.scalar(
            select(ReviewAction.id)
            .where(
                ReviewAction.knowledge_item_id == candidate.id,
                ReviewAction.previous_item_id == active.id,
                ReviewAction.source == ReviewSource.CARRY_FORWARD,
            )
            .limit(1)
        )
        return action is not None

    @staticmethod
    def _change(item, raw_candidate):
        removed = item.change_type == VersionDiffChangeType.REMOVED
        return StructuralReviewChange(
            change_type=item.change_type.value,
            entity_type=item.entity_type,
            canonical_id=item.canonical_id,
            active_item_id=item.active_item_id,
            candidate_item_id=item.candidate_item_id,
            removal_confirmation="unconfirmed" if removed and raw_candidate else None,
            requires_removal_review=removed and raw_candidate,
        )
