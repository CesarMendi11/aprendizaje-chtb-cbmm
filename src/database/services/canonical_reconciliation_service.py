from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import RemovalReconciliationDecisionType
from src.database.models import KnowledgeItem, KnowledgeVersionRecord
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.validator import CanonicalKnowledgeValidator

from .canonical_materialization_service import ENTITY_COLLECTIONS, CanonicalKnowledgeMaterializer
from .removal_reconciliation_plan_service import RemovalReconciliationPlan
from .removal_reconciliation_review_service import (
    RemovalReconciliationReviewError,
    RemovalReconciliationReviewService,
)


class CanonicalReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class CanonicalReconciliationResult:
    active_version_id: str
    active_knowledge_version: str
    candidate_version_id: str
    candidate_knowledge_version: str
    erp_id: str
    candidate_origin: str
    raw_candidate_item_total: int
    active_item_total: int
    reconciled_item_total: int
    retained_from_active_total: int
    confirmed_removed_total: int
    unresolved_total: int
    plan: RemovalReconciliationPlan
    canonical: CanonicalKnowledgeBase


class CanonicalReconciliationService:
    """Materialize a governed reconciliation in memory without persisting it."""

    GENERATOR_VERSION = "canonical-reconciliation-1.0.0"

    def __init__(self, session: Session):
        self.session = session

    def reconcile(self, candidate_version_id: uuid.UUID | str) -> CanonicalReconciliationResult:
        try:
            plan = RemovalReconciliationReviewService(self.session).resolved_plan(
                candidate_version_id
            )
        except RemovalReconciliationReviewError as exc:
            raise CanonicalReconciliationError(str(exc)) from exc
        if plan.unresolved_total:
            raise CanonicalReconciliationError("El plan contiene REMOVED UNRESOLVED.")
        candidate = self.session.get(KnowledgeVersionRecord, uuid.UUID(plan.candidate_version_id))
        active = self.session.get(KnowledgeVersionRecord, uuid.UUID(plan.active_version_id))
        if candidate is None or active is None or candidate.erp_id != active.erp_id:
            raise CanonicalReconciliationError("ACTIVE y RAW candidate no son compatibles.")
        raw = CanonicalKnowledgeMaterializer(self.session).materialize(candidate.id)
        active_canonical = CanonicalKnowledgeMaterializer(self.session).materialize(
            active.id, require_active=True
        )
        if (
            raw.erp_system.id != active_canonical.erp_system.id
            or raw.erp_system.id != candidate.erp_id
        ):
            raise CanonicalReconciliationError("ERP canónico inconsistente con el plan.")

        payload = raw.model_dump(mode="json")
        existing = {
            (entity_type, str(item.get("id")))
            for entity_type, collection in ENTITY_COLLECTIONS.items()
            for item in payload[collection]
        }
        retained = 0
        confirmed = 0
        consumed = set()
        for decision in plan.decisions:
            key = (decision.entity_type, decision.canonical_id)
            if key in consumed:
                raise CanonicalReconciliationError("El plan contiene una decisión duplicada.")
            consumed.add(key)
            if decision.decision == RemovalReconciliationDecisionType.UNRESOLVED:
                raise CanonicalReconciliationError("El plan contiene REMOVED UNRESOLVED.")
            if decision.decision == RemovalReconciliationDecisionType.CONFIRMED_REMOVE:
                confirmed += 1
                continue
            if decision.decision != RemovalReconciliationDecisionType.RETAIN_FROM_ACTIVE:
                raise CanonicalReconciliationError("Decisión de reconciliation no reconocida.")
            if key in existing:
                raise CanonicalReconciliationError(
                    "RAW candidate ya contiene una identidad retenida."
                )
            active_item = self._active_item(active.id, decision)
            collection = ENTITY_COLLECTIONS.get(decision.entity_type)
            if collection is None:
                raise CanonicalReconciliationError(
                    "Tipo canónico no materializable para retención."
                )
            item_payload = copy.deepcopy(active_item.source_payload)
            if item_payload.get("id") != decision.canonical_id:
                raise CanonicalReconciliationError("Payload ACTIVE inconsistente para retención.")
            payload[collection].append(item_payload)
            existing.add(key)
            retained += 1
        if retained != plan.retain_from_active_total or confirmed != plan.confirmed_removed_total:
            raise CanonicalReconciliationError("Los totales de retención no coinciden con el plan.")
        for collection in ENTITY_COLLECTIONS.values():
            payload[collection].sort(key=lambda item: str(item.get("id", "")))
        payload["statistics"] = {
            collection: len(payload[collection]) for collection in ENTITY_COLLECTIONS.values()
        }
        payload["knowledge_version"] = self._knowledge_version(payload)
        payload["generator_version"] = self.GENERATOR_VERSION
        payload["generated_at"] = datetime.now(timezone.utc)
        try:
            canonical = CanonicalKnowledgeBase.model_validate(payload)
        except Exception as exc:
            raise CanonicalReconciliationError(
                "No se pudo materializar canonical reconciliado."
            ) from exc
        errors = CanonicalKnowledgeValidator().errors(canonical)
        if errors:
            raise CanonicalReconciliationError("Canonical reconciliado inválido.")
        raw_total = self._total(raw)
        active_total = self._total(active_canonical)
        reconciled_total = self._total(canonical)
        if reconciled_total != raw_total + retained:
            raise CanonicalReconciliationError("Total reconciliado inconsistente.")
        return CanonicalReconciliationResult(
            active_version_id=plan.active_version_id,
            active_knowledge_version=plan.active_knowledge_version,
            candidate_version_id=plan.candidate_version_id,
            candidate_knowledge_version=plan.candidate_knowledge_version,
            erp_id=candidate.erp_id,
            candidate_origin=plan.candidate_origin,
            raw_candidate_item_total=raw_total,
            active_item_total=active_total,
            reconciled_item_total=reconciled_total,
            retained_from_active_total=retained,
            confirmed_removed_total=confirmed,
            unresolved_total=plan.unresolved_total,
            plan=plan,
            canonical=canonical,
        )

    def _active_item(self, active_id, decision):
        try:
            item_id = uuid.UUID(str(decision.active_item_id))
        except (TypeError, ValueError) as exc:
            raise CanonicalReconciliationError("active_item_id inválido en plan.") from exc
        matches = list(
            self.session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.id == item_id,
                    KnowledgeItem.knowledge_version_id == active_id,
                    KnowledgeItem.entity_type == decision.entity_type,
                    KnowledgeItem.canonical_id == decision.canonical_id,
                )
            )
        )
        if len(matches) != 1:
            raise CanonicalReconciliationError(
                "La retención no resuelve exactamente un item ACTIVE."
            )
        return matches[0]

    @staticmethod
    def _total(canonical):
        return 1 + sum(
            len(getattr(canonical, collection)) for collection in ENTITY_COLLECTIONS.values()
        )

    @staticmethod
    def _knowledge_version(payload):
        functional = {
            "erp_system": payload["erp_system"],
            **{
                collection: payload[collection]
                for collection in ENTITY_COLLECTIONS.values()
                if collection != "evidence"
            },
        }
        return content_hash(functional)[:16]
