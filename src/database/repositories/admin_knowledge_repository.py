from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import String, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload, selectinload

from src.database.enums import KnowledgeVersionStatus
from src.database.models import (
    ERPSystemRecord,
    KnowledgeItem,
    KnowledgeVersionRecord,
    ReviewAction,
    SemanticProposal,
    SemanticReviewAction,
)
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.models import Module, Screen

ELIGIBLE = (ReviewStatus.APPROVED, ReviewStatus.CORRECTED)


@dataclass(frozen=True)
class EffectiveModule:
    item: KnowledgeItem
    value: Module | None
    payload: dict[str, Any] | None
    diagnostic: str | None

    @property
    def module_id(self) -> str:
        return self.value.id if self.value else self.item.canonical_id

    @property
    def name(self) -> str | None:
        return self.value.name if self.value else self.item.title

    @property
    def route(self) -> str | None:
        return self.value.route_prefix if self.value else self.item.route


@dataclass(frozen=True)
class EffectiveScreen:
    item: KnowledgeItem
    value: Screen | None
    payload: dict[str, Any] | None
    diagnostic: str | None

    @property
    def screen_id(self) -> str:
        return self.value.id if self.value else self.item.canonical_id

    @property
    def title(self) -> str | None:
        return self.value.title if self.value else self.item.title

    @property
    def route(self) -> str | None:
        return self.value.route if self.value else self.item.route

    @property
    def module_id(self) -> str | None:
        return self.value.module_id if self.value else None


@dataclass(frozen=True)
class AdminKnowledgeSnapshot:
    erp: ERPSystemRecord
    version: KnowledgeVersionRecord
    modules: tuple[EffectiveModule, ...]
    screens: tuple[EffectiveScreen, ...]
    proposals: dict[uuid.UUID, tuple[tuple[SemanticProposal, int], ...]]


@dataclass(frozen=True)
class AdminScreenPageRow:
    erp: ERPSystemRecord
    version: KnowledgeVersionRecord
    screen: EffectiveScreen
    module: EffectiveModule | None
    proposals: tuple[tuple[SemanticProposal, int], ...]
    semantic_actions: dict[uuid.UUID, tuple[SemanticReviewAction, ...]]


class AdminKnowledgeRepository:
    """Read-only, bounded-query projection for the provisional admin console."""

    def __init__(self, session: Session):
        self.session = session

    def snapshots(
        self,
        *,
        erp_id: str | None = None,
        knowledge_version_id: uuid.UUID | None = None,
    ) -> tuple[AdminKnowledgeSnapshot, ...]:
        versions = self._versions(erp_id=erp_id, version_id=knowledge_version_id)
        if not versions:
            return ()
        version_ids = [version.id for version in versions]
        items = list(
            self.session.scalars(
                select(KnowledgeItem)
                .where(
                    KnowledgeItem.knowledge_version_id.in_(version_ids),
                    KnowledgeItem.entity_type.in_(("module", "screen")),
                    KnowledgeItem.current_review_status.in_(ELIGIBLE),
                )
                .order_by(KnowledgeItem.knowledge_version_id, KnowledgeItem.canonical_id)
            )
        )
        corrections = self._latest_corrections([item.id for item in items])
        proposals = self._proposals(version_ids=version_ids)
        by_version: dict[uuid.UUID, list[KnowledgeItem]] = defaultdict(list)
        for item in items:
            by_version[item.knowledge_version_id].append(item)
        result = []
        for version in versions:
            modules = []
            screens = []
            for item in by_version[version.id]:
                payload = corrections.get(item.id) or item.source_payload
                if item.entity_type == "module":
                    modules.append(self._module(item, payload))
                else:
                    screens.append(self._screen(item, payload))
            result.append(
                AdminKnowledgeSnapshot(
                    erp=version.erp,
                    version=version,
                    modules=tuple(modules),
                    screens=tuple(screens),
                    proposals=proposals,
                )
            )
        return tuple(result)

    def list_screen_page(
        self,
        *,
        erp_id: str | None,
        module_id: str | None,
        text: str | None,
        semantic_status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AdminScreenPageRow], int]:
        """Page in SQL; semantic validation scans bounded SQL batches for exact totals."""
        version_ids = select(self._active_version_ids().c.id)
        latest_correction = self._latest_correction_subquery()
        latest_proposal = self._latest_proposal_subquery()
        effective_module = func.coalesce(
            cast(latest_correction.c.corrected_payload["module_id"].as_string(), String),
            KnowledgeItem.parent_canonical_id,
        )
        effective_title = func.coalesce(
            cast(latest_correction.c.corrected_payload["title"].as_string(), String),
            KnowledgeItem.title,
            "",
        )
        effective_normalized_title = func.coalesce(
            cast(
                latest_correction.c.corrected_payload["normalized_title"].as_string(),
                String,
            ),
            KnowledgeItem.normalized_title,
            func.lower(effective_title),
        )
        effective_route = func.coalesce(
            cast(latest_correction.c.corrected_payload["route"].as_string(), String),
            KnowledgeItem.route,
            "",
        )
        filters = [
            KnowledgeItem.entity_type == "screen",
            KnowledgeItem.current_review_status.in_(ELIGIBLE),
            KnowledgeItem.knowledge_version_id.in_(version_ids),
        ]
        if erp_id:
            filters.append(KnowledgeVersionRecord.erp_id == erp_id)
        if module_id:
            filters.append(effective_module == module_id)
        if text:
            pattern = f"%{text.casefold()}%"
            filters.append(
                or_(
                    func.lower(effective_normalized_title).like(pattern),
                    func.lower(effective_title).like(pattern),
                    func.lower(effective_route).like(pattern),
                    func.lower(KnowledgeItem.canonical_id).like(pattern),
                )
            )
        if semantic_status == "no_proposal":
            filters.append(latest_proposal.c.proposal_id.is_(None))
        elif semantic_status in {"pending_review", "approved", "corrected", "rejected"}:
            filters.append(latest_proposal.c.current_review_status == semantic_status)

        base = (
            select(KnowledgeItem.id)
            .join(
                KnowledgeVersionRecord,
                KnowledgeVersionRecord.id == KnowledgeItem.knowledge_version_id,
            )
            .outerjoin(
                latest_correction,
                and_(
                    latest_correction.c.knowledge_item_id == KnowledgeItem.id,
                    latest_correction.c.rn == 1,
                ),
            )
            .outerjoin(
                latest_proposal,
                and_(
                    latest_proposal.c.screen_knowledge_item_id == KnowledgeItem.id,
                    latest_proposal.c.rn == 1,
                ),
            )
            .where(*filters)
        )
        ordered = base.order_by(
            func.lower(effective_normalized_title),
            func.lower(effective_title),
            KnowledgeItem.canonical_id,
        )
        if semantic_status is None:
            total = int(self.session.scalar(select(func.count()).select_from(base.subquery())) or 0)
            page_ids = list(self.session.scalars(ordered.limit(limit).offset(offset)))
            return self._page_rows(page_ids), total

        # Pydantic validity and correction history cannot be expressed faithfully in
        # portable SQL. Scan candidate IDs in bounded SQL batches, retain only the
        # requested page, and continue the exact count with constant peak memory.
        from src.api.admin_knowledge_serializers import tree_screen

        batch_size = 200
        scanned = 0
        matched = 0
        page: list[AdminScreenPageRow] = []
        while True:
            batch_ids = list(self.session.scalars(ordered.limit(batch_size).offset(scanned)))
            if not batch_ids:
                break
            for row in self._page_rows(batch_ids):
                state = tree_screen(
                    row.screen,
                    row.proposals,
                    row.semantic_actions,
                ).semantic_state
                if state == semantic_status:
                    if offset <= matched < offset + limit:
                        page.append(row)
                    matched += 1
            scanned += len(batch_ids)
        return page, matched

    def screen_snapshot(
        self, screen_id: str, *, knowledge_version_id: uuid.UUID | None = None
    ) -> tuple[AdminKnowledgeSnapshot, EffectiveScreen] | None:
        query = (
            select(KnowledgeItem)
            .join(KnowledgeVersionRecord)
            .where(
                KnowledgeItem.canonical_id == screen_id,
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.current_review_status.in_(ELIGIBLE),
            )
            .options(
                joinedload(KnowledgeItem.knowledge_version).joinedload(KnowledgeVersionRecord.erp)
            )
            .order_by(
                KnowledgeVersionRecord.imported_at.desc(),
                KnowledgeVersionRecord.id.desc(),
            )
            .limit(1)
        )
        if knowledge_version_id is not None:
            query = query.where(KnowledgeVersionRecord.id == knowledge_version_id)
        else:
            query = query.where(
                KnowledgeVersionRecord.id.in_(select(self._active_version_ids().c.id))
            )
        item = self.session.scalar(query)
        if item is None:
            return None
        correction = self._latest_corrections([item.id]).get(item.id)
        screen = self._screen(item, correction or item.source_payload)
        modules = ()
        if screen.module_id:
            module_item = self.session.scalar(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == item.knowledge_version_id,
                    KnowledgeItem.entity_type == "module",
                    KnowledgeItem.canonical_id == screen.module_id,
                    KnowledgeItem.current_review_status.in_(ELIGIBLE),
                )
            )
            if module_item:
                module_correction = self._latest_corrections([module_item.id]).get(module_item.id)
                modules = (
                    self._module(
                        module_item,
                        module_correction or module_item.source_payload,
                    ),
                )
        proposals = self._proposals(screen_item_ids=[item.id])
        snapshot = AdminKnowledgeSnapshot(
            erp=item.knowledge_version.erp,
            version=item.knowledge_version,
            modules=modules,
            screens=(screen,),
            proposals=proposals,
        )
        return snapshot, screen

    def screen_structure_snapshot(
        self,
        version_id: uuid.UUID,
        screen: EffectiveScreen,
        module: EffectiveModule | None,
    ) -> tuple[KnowledgeItem, ...]:
        """Load only the effective structural neighborhood of one screen."""
        correction = self._latest_correction_subquery()

        def effective_ref(key: str):
            return func.coalesce(
                cast(correction.c.corrected_payload[key].as_string(), String),
                cast(KnowledgeItem.source_payload[key].as_string(), String),
            )

        common = (
            select(KnowledgeItem)
            .outerjoin(
                correction,
                and_(
                    correction.c.knowledge_item_id == KnowledgeItem.id,
                    correction.c.rn == 1,
                ),
            )
            .where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.current_review_status.in_(ELIGIBLE),
            )
        )
        related = list(
            self.session.scalars(
                common.where(
                    KnowledgeItem.entity_type.in_(
                        ("field", "control", "table", "ui_state", "event")
                    ),
                    effective_ref("screen_id") == screen.screen_id,
                )
            )
        )
        table_ids = [item.canonical_id for item in related if item.entity_type == "table"]
        columns = (
            list(
                self.session.scalars(
                    common.where(
                        KnowledgeItem.entity_type == "table_column",
                        effective_ref("table_id").in_(table_ids),
                    )
                )
            )
            if table_ids
            else []
        )
        state_ids = [item.canonical_id for item in related if item.entity_type == "ui_state"]
        transitions = (
            list(
                self.session.scalars(
                    common.where(
                        KnowledgeItem.entity_type == "transition",
                        or_(
                            effective_ref("source_state_id").in_(state_ids),
                            effective_ref("target_state_id").in_(state_ids),
                        ),
                    )
                )
            )
            if state_ids
            else []
        )
        selected = [screen.item]
        if module:
            selected.append(module.item)
        selected.extend(related)
        selected.extend(columns)
        selected.extend(transitions)
        payloads = self.effective_payloads(tuple(selected))
        evidence_ids = {
            evidence_id
            for payload in payloads.values()
            if isinstance(payload, dict)
            for evidence_id in payload.get("evidence_ids", [])
            if isinstance(evidence_id, str)
        }
        evidence = (
            list(
                self.session.scalars(
                    select(KnowledgeItem).where(
                        KnowledgeItem.knowledge_version_id == version_id,
                        KnowledgeItem.entity_type == "evidence",
                        KnowledgeItem.canonical_id.in_(evidence_ids),
                    )
                )
            )
            if evidence_ids
            else []
        )
        return tuple([*selected, *evidence])

    def effective_payloads(self, items: tuple[KnowledgeItem, ...]) -> dict[uuid.UUID, dict]:
        corrections = self._latest_corrections([item.id for item in items])
        return {item.id: corrections.get(item.id) or item.source_payload for item in items}

    def navigation_ids(self, version_id: uuid.UUID, module_id: str | None) -> tuple[str, ...]:
        correction = self._latest_correction_subquery()
        effective_module = func.coalesce(
            cast(correction.c.corrected_payload["module_id"].as_string(), String),
            KnowledgeItem.parent_canonical_id,
        )
        effective_title = func.coalesce(
            cast(correction.c.corrected_payload["normalized_title"].as_string(), String),
            cast(correction.c.corrected_payload["title"].as_string(), String),
            KnowledgeItem.normalized_title,
            KnowledgeItem.title,
            "",
        )
        module_filter = (
            effective_module.is_(None) if module_id is None else effective_module == module_id
        )
        return tuple(
            self.session.scalars(
                select(KnowledgeItem.canonical_id)
                .outerjoin(
                    correction,
                    and_(
                        correction.c.knowledge_item_id == KnowledgeItem.id,
                        correction.c.rn == 1,
                    ),
                )
                .where(
                    KnowledgeItem.knowledge_version_id == version_id,
                    KnowledgeItem.entity_type == "screen",
                    KnowledgeItem.current_review_status.in_(ELIGIBLE),
                    module_filter,
                )
                .order_by(func.lower(effective_title), KnowledgeItem.canonical_id)
            )
        )

    def _page_rows(self, item_ids: list[uuid.UUID]) -> list[AdminScreenPageRow]:
        if not item_ids:
            return []
        items = list(
            self.session.scalars(
                select(KnowledgeItem)
                .where(KnowledgeItem.id.in_(item_ids))
                .options(
                    joinedload(KnowledgeItem.knowledge_version).joinedload(
                        KnowledgeVersionRecord.erp
                    )
                )
            )
        )
        corrections = self._latest_corrections([item.id for item in items])
        screens = {
            item.id: self._screen(item, corrections.get(item.id) or item.source_payload)
            for item in items
        }
        module_ids = {screen.module_id for screen in screens.values() if screen.module_id}
        module_items = (
            list(
                self.session.scalars(
                    select(KnowledgeItem).where(
                        KnowledgeItem.knowledge_version_id.in_(
                            {item.knowledge_version_id for item in items}
                        ),
                        KnowledgeItem.entity_type == "module",
                        KnowledgeItem.canonical_id.in_(module_ids),
                        KnowledgeItem.current_review_status.in_(ELIGIBLE),
                    )
                )
            )
            if module_ids
            else []
        )
        module_corrections = self._latest_corrections([item.id for item in module_items])
        modules = {
            (item.knowledge_version_id, item.canonical_id): self._module(
                item, module_corrections.get(item.id) or item.source_payload
            )
            for item in module_items
        }
        proposals = self._proposals(screen_item_ids=item_ids)
        actions = defaultdict(list)
        for rows in proposals.values():
            for proposal, _count in rows:
                actions[proposal.id].extend(proposal.review_actions)
        by_id = {item.id: item for item in items}
        return [
            AdminScreenPageRow(
                erp=by_id[item_id].knowledge_version.erp,
                version=by_id[item_id].knowledge_version,
                screen=screens[item_id],
                module=modules.get(
                    (by_id[item_id].knowledge_version_id, screens[item_id].module_id)
                ),
                proposals=proposals.get(item_id, ()),
                semantic_actions={key: tuple(value) for key, value in actions.items()},
            )
            for item_id in item_ids
        ]

    def _versions(self, *, erp_id: str | None, version_id: uuid.UUID | None):
        query = select(KnowledgeVersionRecord).options(joinedload(KnowledgeVersionRecord.erp))
        if version_id is not None:
            query = query.where(KnowledgeVersionRecord.id == version_id)
        else:
            query = query.where(
                KnowledgeVersionRecord.id.in_(select(self._active_version_ids().c.id))
            )
        if erp_id is not None:
            query = query.where(KnowledgeVersionRecord.erp_id == erp_id)
        return list(self.session.scalars(query.order_by(KnowledgeVersionRecord.erp_id)))

    def _active_version_ids(self):
        ranked = (
            select(
                KnowledgeVersionRecord.id.label("id"),
                func.row_number()
                .over(
                    partition_by=KnowledgeVersionRecord.erp_id,
                    order_by=(
                        KnowledgeVersionRecord.imported_at.desc(),
                        KnowledgeVersionRecord.id.desc(),
                    ),
                )
                .label("rn"),
            )
            .where(KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE)
            .subquery()
        )
        return select(ranked.c.id).where(ranked.c.rn == 1).subquery()

    def _latest_correction_subquery(self):
        correction = aliased(ReviewAction)
        later = aliased(ReviewAction)
        later_reset = exists(
            select(later.id).where(
                later.knowledge_item_id == correction.knowledge_item_id,
                later.action == "reset_to_pending",
                or_(
                    later.created_at > correction.created_at,
                    and_(
                        later.created_at == correction.created_at,
                        later.id > correction.id,
                    ),
                ),
            )
        )
        ranked = (
            select(
                correction.knowledge_item_id,
                correction.corrected_payload,
                func.row_number()
                .over(
                    partition_by=correction.knowledge_item_id,
                    order_by=(correction.created_at.desc(), correction.id.desc()),
                )
                .label("rn"),
            )
            .where(correction.corrected_payload.is_not(None), ~later_reset)
            .subquery()
        )
        return (
            select(
                ranked.c.knowledge_item_id,
                ranked.c.corrected_payload,
                ranked.c.rn,
            )
            .where(ranked.c.rn == 1, ranked.c.corrected_payload.is_not(None))
            .subquery()
        )

    def _latest_proposal_subquery(self):
        return select(
            SemanticProposal.id.label("proposal_id"),
            SemanticProposal.screen_knowledge_item_id,
            SemanticProposal.current_review_status,
            func.row_number()
            .over(
                partition_by=SemanticProposal.screen_knowledge_item_id,
                order_by=(SemanticProposal.created_at.desc(), SemanticProposal.semantic_id.desc()),
            )
            .label("rn"),
        ).subquery()

    def _latest_corrections(self, item_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
        if not item_ids:
            return {}
        actions = self.session.execute(
            select(
                ReviewAction.knowledge_item_id, ReviewAction.action, ReviewAction.corrected_payload
            )
            .where(ReviewAction.knowledge_item_id.in_(item_ids))
            .order_by(ReviewAction.created_at, ReviewAction.id)
        )
        effective = {}
        for item_id, action, payload in actions:
            if str(action) == "reset_to_pending":
                effective.pop(item_id, None)
            elif payload is not None:
                effective[item_id] = payload
        return effective

    def _proposals(self, *, version_ids=None, screen_item_ids=None):
        counts = (
            select(
                SemanticReviewAction.semantic_proposal_id.label("proposal_id"),
                func.count(SemanticReviewAction.id).label("action_count"),
            )
            .group_by(SemanticReviewAction.semantic_proposal_id)
            .subquery()
        )
        query = select(SemanticProposal, func.coalesce(counts.c.action_count, 0)).outerjoin(
            counts, counts.c.proposal_id == SemanticProposal.id
        )
        if version_ids is not None:
            query = query.where(SemanticProposal.knowledge_version_id.in_(version_ids))
        if screen_item_ids is not None:
            query = query.where(SemanticProposal.screen_knowledge_item_id.in_(screen_item_ids))
        query = query.options(
            joinedload(SemanticProposal.knowledge_version).joinedload(KnowledgeVersionRecord.erp),
            joinedload(SemanticProposal.screen_knowledge_item),
            selectinload(SemanticProposal.review_actions),
        ).order_by(
            SemanticProposal.screen_knowledge_item_id,
            SemanticProposal.created_at,
            SemanticProposal.semantic_id,
        )
        grouped = defaultdict(list)
        for proposal, count in self.session.execute(query):
            grouped[proposal.screen_knowledge_item_id].append((proposal, int(count)))
        return {key: tuple(value) for key, value in grouped.items()}

    def screen_history(self, proposal_ids: list[uuid.UUID]):
        if not proposal_ids:
            return []
        return list(
            self.session.scalars(
                select(SemanticReviewAction)
                .where(SemanticReviewAction.semantic_proposal_id.in_(proposal_ids))
                .order_by(SemanticReviewAction.created_at, SemanticReviewAction.id)
            )
        )

    def erp_exists(self, erp_id: str) -> bool:
        return (
            self.session.scalar(select(ERPSystemRecord.id).where(ERPSystemRecord.id == erp_id))
            is not None
        )

    def version(self, version_id: uuid.UUID):
        return self.session.get(KnowledgeVersionRecord, version_id)

    @staticmethod
    def _module(item, payload):
        try:
            return EffectiveModule(item, Module.model_validate(payload), payload, None)
        except (ValidationError, TypeError, ValueError):
            return EffectiveModule(
                item,
                None,
                None,
                "El módulo persistido no cumple el esquema canónico.",
            )

    @staticmethod
    def _screen(item, payload):
        try:
            return EffectiveScreen(item, Screen.model_validate(payload), payload, None)
        except (ValidationError, TypeError, ValueError):
            return EffectiveScreen(
                item, None, None, "La pantalla persistida no cumple el esquema canónico."
            )
