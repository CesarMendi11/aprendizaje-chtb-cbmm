from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus, ReviewSource
from erp_assistant.persistence.postgres.models import KnowledgeItem, KnowledgeVersionRecord
from erp_assistant.structural.canonical.enums import ReviewStatus

from .knowledge_review_service import KnowledgeReviewService
from .structural_ownership import StructuralOwner, StructuralOwnershipResolver

ScopeType = Literal["screen", "module", "system", "unscoped"]
PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


class StructuralPublicationReviewError(ValueError):
    pass


class StructuralPublicationReviewConflictError(StructuralPublicationReviewError):
    pass


@dataclass(frozen=True)
class StructuralPublicationReviewItem:
    item_id: str
    entity_type: str
    canonical_id: str
    title: str | None
    route: str | None
    review_status: str
    review_revision: int
    content_hash: str


@dataclass(frozen=True)
class StructuralPublicationReviewPackage:
    scope_type: ScopeType
    scope_id: str
    title: str | None
    route: str | None
    module_id: str | None
    module_path: tuple[str, ...]
    status_counts: dict[str, int]
    entity_counts: dict[str, int]
    pending_count: int
    publishable_count: int
    rejected_count: int
    review_required: bool
    package_hash: str
    review_items: tuple[StructuralPublicationReviewItem, ...]


@dataclass(frozen=True)
class StructuralPublicationReviewSummary:
    knowledge_version_id: str
    knowledge_version: str
    erp_id: str
    version_status: str
    status_counts: dict[str, int]
    publishable_count: int
    pending_count: int
    rejected_count: int
    package_count: int
    packages: tuple[StructuralPublicationReviewPackage, ...]
    total: int
    limit: int | None
    offset: int
    next_offset: int | None


@dataclass(frozen=True)
class StructuralPublicationApprovalResult:
    approved_count: int
    package: StructuralPublicationReviewPackage


class StructuralPublicationReviewService:
    """Governed review packages for closing publication coverage of the current ACTIVE."""

    _SCOPE_ORDER = {"system": 0, "module": 1, "screen": 2, "unscoped": 3}

    def __init__(self, session: Session):
        self.session = session
        self.ownership = StructuralOwnershipResolver()

    def build(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        pending_only: bool = False,
        scope_type: ScopeType | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> StructuralPublicationReviewSummary:
        version = self._active_version(knowledge_version_id)
        items = self._items(version.id)
        packages = self._packages(items)
        visible = [
            package
            for package in packages
            if (not pending_only or package.pending_count > 0)
            and (scope_type is None or package.scope_type == scope_type)
        ]
        total = len(visible)
        page = tuple(visible[offset:] if limit is None else visible[offset : offset + limit])
        status_counts = Counter(str(item.current_review_status) for item in items.values())
        publishable_count = sum(
            count
            for status, count in status_counts.items()
            if status in {str(value) for value in PUBLISHABLE}
        )
        return StructuralPublicationReviewSummary(
            knowledge_version_id=str(version.id),
            knowledge_version=version.knowledge_version,
            erp_id=version.erp_id,
            version_status=str(version.status),
            status_counts=dict(sorted(status_counts.items())),
            publishable_count=publishable_count,
            pending_count=status_counts[str(ReviewStatus.PENDING_REVIEW)],
            rejected_count=status_counts[str(ReviewStatus.REJECTED)],
            package_count=len(packages),
            packages=page,
            total=total,
            limit=limit,
            offset=offset,
            next_offset=offset + len(page) if offset + len(page) < total else None,
        )

    def approve_pending(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        scope_type: ScopeType,
        scope_id: str,
        expected_package_hash: str,
        reviewer: str,
        reason: str,
    ) -> StructuralPublicationApprovalResult:
        version = self._active_version(knowledge_version_id, for_update=True)
        items = self._items(version.id, for_update=True)
        grouped = self._group(items)
        key = (scope_type, scope_id)
        selected = grouped.get(key)
        if not selected:
            raise StructuralPublicationReviewError("Paquete de publicación no encontrado.")
        package = self._package(key, selected, items)
        if package.package_hash != expected_package_hash:
            raise StructuralPublicationReviewConflictError(
                "El paquete cambió; vuelva a inspeccionarlo antes de aprobar."
            )
        pending = [
            item for item in selected if item.current_review_status == ReviewStatus.PENDING_REVIEW
        ]
        if not pending:
            raise StructuralPublicationReviewError("El paquete no contiene elementos pendientes.")
        service = KnowledgeReviewService(self.session)
        try:
            for item in sorted(pending, key=self._item_sort_key):
                service.approve(
                    item.id,
                    reviewer=reviewer,
                    notes=reason,
                    expected_revision=item.review_revision,
                    source=ReviewSource.API,
                )
        except ValueError as exc:
            raise StructuralPublicationReviewConflictError(str(exc)) from exc
        refreshed = self._package(key, selected, items)
        return StructuralPublicationApprovalResult(len(pending), refreshed)

    def _active_version(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        for_update: bool = False,
    ) -> KnowledgeVersionRecord:
        try:
            version_id = uuid.UUID(str(knowledge_version_id))
        except (TypeError, ValueError) as exc:
            raise LookupError("KnowledgeVersion no encontrada") from exc
        version = self.session.get(
            KnowledgeVersionRecord,
            version_id,
            with_for_update=for_update,
        )
        if version is None:
            raise LookupError("KnowledgeVersion no encontrada")
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise StructuralPublicationReviewError(
                "La revisión de cobertura de publicación sólo admite la versión ACTIVE."
            )
        return version

    def _items(
        self,
        version_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> dict[tuple[str, str], KnowledgeItem]:
        query = select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == version_id)
        if for_update:
            query = query.with_for_update()
        return {
            (item.entity_type, item.canonical_id): item
            for item in self.session.scalars(query)
        }

    def _packages(
        self, items: dict[tuple[str, str], KnowledgeItem]
    ) -> list[StructuralPublicationReviewPackage]:
        grouped = self._group(items)
        packages = [self._package(key, values, items) for key, values in grouped.items()]
        packages.sort(
            key=lambda package: (
                self._SCOPE_ORDER[package.scope_type],
                package.module_path,
                package.route or "",
                package.title or "",
                package.scope_id,
            )
        )
        return packages

    def _group(self, items: dict[tuple[str, str], KnowledgeItem]):
        grouped: dict[tuple[ScopeType, str], list[KnowledgeItem]] = defaultdict(list)
        for item in items.values():
            owner = self.ownership.owner_for_item(item, items)
            key = self._scope_key(owner)
            grouped[key].append(item)
        return grouped

    @staticmethod
    def _scope_key(owner: StructuralOwner | None) -> tuple[ScopeType, str]:
        if owner is None:
            return ("unscoped", "unscoped")
        return (owner.scope_type, owner.scope_id)  # type: ignore[return-value]

    def _package(self, key, selected, items):
        scope_type, scope_id = key
        owner_item = items.get((scope_type if scope_type != "system" else "erp_system", scope_id))
        payload = dict(owner_item.source_payload or {}) if owner_item is not None else {}
        status_counts = Counter(str(item.current_review_status) for item in selected)
        entity_counts = Counter(item.entity_type for item in selected)
        pending_count = status_counts[str(ReviewStatus.PENDING_REVIEW)]
        rejected_count = status_counts[str(ReviewStatus.REJECTED)]
        publishable_count = sum(
            status_counts[str(status)] for status in PUBLISHABLE
        )
        review_items = tuple(
            self._review_item(item)
            for item in sorted(selected, key=self._item_sort_key)
            if item.current_review_status not in PUBLISHABLE
        )
        module_id = self._module_id(scope_type, scope_id, payload)
        return StructuralPublicationReviewPackage(
            scope_type=scope_type,
            scope_id=scope_id,
            title=owner_item.title if owner_item is not None else "Sin alcance estructural",
            route=(
                owner_item.route
                if owner_item is not None and owner_item.route
                else self._route_prefix(payload)
            ),
            module_id=module_id,
            module_path=self._module_path(module_id, items),
            status_counts=dict(sorted(status_counts.items())),
            entity_counts=dict(sorted(entity_counts.items())),
            pending_count=pending_count,
            publishable_count=publishable_count,
            rejected_count=rejected_count,
            review_required=pending_count > 0 or rejected_count > 0,
            package_hash=self._package_hash(selected),
            review_items=review_items,
        )

    @staticmethod
    def _route_prefix(payload: dict) -> str | None:
        value = payload.get("route_prefix")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _module_id(scope_type: ScopeType, scope_id: str, payload: dict) -> str | None:
        if scope_type == "module":
            return scope_id
        module_id = payload.get("module_id")
        return module_id if isinstance(module_id, str) and module_id else None

    @staticmethod
    def _module_path(module_id, items):
        if not module_id:
            return ()
        path, seen, current = [], set(), module_id
        while current:
            if current in seen:
                raise StructuralPublicationReviewError(
                    "La jerarquía de módulos contiene un ciclo."
                )
            seen.add(current)
            module = items.get(("module", current))
            if module is None:
                raise StructuralPublicationReviewError(
                    "El alcance referencia un módulo inexistente o un padre no resoluble."
                )
            path.append(current)
            parent = (module.source_payload or {}).get("parent_module_id")
            current = parent if isinstance(parent, str) and parent else None
        return tuple(reversed(path))

    @staticmethod
    def _item_sort_key(item: KnowledgeItem):
        return item.entity_type, item.title or "", item.canonical_id

    @staticmethod
    def _review_item(item: KnowledgeItem) -> StructuralPublicationReviewItem:
        return StructuralPublicationReviewItem(
            item_id=str(item.id),
            entity_type=item.entity_type,
            canonical_id=item.canonical_id,
            title=item.title,
            route=item.route,
            review_status=str(item.current_review_status),
            review_revision=item.review_revision,
            content_hash=item.content_hash,
        )

    @staticmethod
    def _package_hash(items: list[KnowledgeItem]) -> str:
        payload = [
            {
                "item_id": str(item.id),
                "entity_type": item.entity_type,
                "canonical_id": item.canonical_id,
                "content_hash": item.content_hash,
                "review_status": str(item.current_review_status),
                "review_revision": item.review_revision,
            }
            for item in sorted(items, key=StructuralPublicationReviewService._item_sort_key)
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
