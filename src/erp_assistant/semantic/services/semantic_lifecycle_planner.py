from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus, SemanticType
from erp_assistant.persistence.postgres.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
)
from erp_assistant.persistence.postgres.repositories import SemanticProposalRepository
from erp_assistant.projections.replacement_service import (
    ProjectionReplacementError,
    ProjectionReplacementService,
)
from erp_assistant.semantic.eligibility import evaluate_screen_semantic_eligibility
from erp_assistant.semantic.evidence import ScreenEvidenceBuilder
from erp_assistant.semantic.evidence.screen_evidence_builder import ScreenEvidenceError
from erp_assistant.semantic.prompts import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from erp_assistant.structural.canonical.enums import ReviewStatus

from .semantic_effective_payload_service import SemanticEffectivePayloadService
from .semantic_exceptions import (
    SemanticHistoryIntegrityError,
    SemanticProposalNotFoundError,
)
from .semantic_payloads import (
    canonical_json_hash,
    semantic_evidence_compatibility_hash,
)


class SemanticLifecycleDecision(StrEnum):
    GENERATE = "generate"
    CARRY_FORWARD = "carry_forward"
    REINFERENCE_REQUIRED = "reinference_required"
    BLOCKED = "blocked"


class SemanticLifecyclePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: SemanticLifecycleDecision
    reasons: tuple[str, ...] = ()
    target_knowledge_version_id: uuid.UUID | None = None
    target_screen_knowledge_item_id: uuid.UUID | None = None
    target_screen_id: str | None = None
    target_evidence_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    target_compatibility_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_semantic_proposal_id: uuid.UUID | None = None
    source_knowledge_version_id: uuid.UUID | None = None
    source_review_status: ReviewStatus | None = None
    source_review_revision: int | None = Field(default=None, ge=0)
    source_effective_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_evidence_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_compatibility_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SemanticLifecyclePlanner:
    """Read-only, deterministic lifecycle decision for screen-purpose semantics."""

    PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}

    def __init__(
        self,
        session: Session,
        *,
        evidence_builder: ScreenEvidenceBuilder | None = None,
        replacement_service: ProjectionReplacementService | None = None,
        effective_payload_service: SemanticEffectivePayloadService | None = None,
    ):
        self.session = session
        self.evidence_builder = evidence_builder or ScreenEvidenceBuilder(session)
        self.replacement = replacement_service or ProjectionReplacementService(session)
        self.effective = effective_payload_service or SemanticEffectivePayloadService(session)
        self.proposals = SemanticProposalRepository(session)

    def plan(
        self,
        knowledge_version_id: uuid.UUID | str,
        screen_knowledge_item_id: uuid.UUID | str,
        *,
        generation_model: str,
    ) -> SemanticLifecyclePlan:
        version_id = self._uuid(knowledge_version_id)
        screen_item_id = self._uuid(screen_knowledge_item_id)
        model = generation_model.strip() if isinstance(generation_model, str) else ""

        if version_id is None:
            return self._blocked("invalid_target_version_id")
        if screen_item_id is None:
            return self._blocked(
                "invalid_target_screen_item_id",
                target_knowledge_version_id=version_id,
            )
        if not model:
            return self._blocked(
                "missing_generation_model",
                target_knowledge_version_id=version_id,
                target_screen_knowledge_item_id=screen_item_id,
            )

        version = self.session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            return self._blocked(
                "target_version_missing",
                target_knowledge_version_id=version_id,
                target_screen_knowledge_item_id=screen_item_id,
            )
        if version.status != KnowledgeVersionStatus.ACTIVE:
            return self._blocked(
                "target_version_not_active",
                target_knowledge_version_id=version.id,
                target_screen_knowledge_item_id=screen_item_id,
            )

        screen = self.session.get(KnowledgeItem, screen_item_id)
        if screen is None:
            return self._blocked(
                "target_screen_missing",
                target_knowledge_version_id=version.id,
                target_screen_knowledge_item_id=screen_item_id,
            )
        if screen.entity_type != "screen":
            return self._blocked(
                "target_item_not_screen",
                target_knowledge_version_id=version.id,
                target_screen_knowledge_item_id=screen.id,
                target_screen_id=screen.canonical_id,
            )
        if screen.knowledge_version_id != version.id:
            return self._blocked(
                "target_screen_version_mismatch",
                target_knowledge_version_id=version.id,
                target_screen_knowledge_item_id=screen.id,
                target_screen_id=screen.canonical_id,
            )
        if screen.current_review_status not in self.PUBLISHABLE:
            return self._blocked(
                "target_screen_not_publishable",
                target_knowledge_version_id=version.id,
                target_screen_knowledge_item_id=screen.id,
                target_screen_id=screen.canonical_id,
            )

        try:
            target_package = self.evidence_builder.build(version.id, screen.id)
        except ScreenEvidenceError as exc:
            return self._blocked(
                f"target_evidence_unavailable:{type(exc).__name__}",
                target_knowledge_version_id=version.id,
                target_screen_knowledge_item_id=screen.id,
                target_screen_id=screen.canonical_id,
            )

        eligibility = evaluate_screen_semantic_eligibility(target_package)
        target_compatibility_hash = semantic_evidence_compatibility_hash(target_package)
        base = {
            "target_knowledge_version_id": version.id,
            "target_screen_knowledge_item_id": screen.id,
            "target_screen_id": screen.canonical_id,
            "target_evidence_hash": target_package.evidence_hash,
            "target_compatibility_hash": target_compatibility_hash,
        }
        if not eligibility.eligible:
            return self._blocked(
                *(f"target_ineligible:{reason}" for reason in eligibility.reasons),
                **base,
            )

        existing_target = self.proposals.list(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            limit=1000,
        )
        if existing_target:
            current_generation = self.proposals.get_by_generation_identity(
                knowledge_version_id=version.id,
                screen_knowledge_item_id=screen.id,
                semantic_type=SemanticType.SCREEN_PURPOSE,
                evidence_hash=target_package.evidence_hash,
                prompt_hash=PROMPT_HASH,
                generation_model=model,
                generation_parameters_hash=GENERATION_PARAMETERS_HASH,
            )
            if current_generation is not None:
                return self._blocked(
                    "target_semantic_already_exists",
                    **base,
                )
            return SemanticLifecyclePlan(
                decision=SemanticLifecycleDecision.GENERATE,
                reasons=("same_version_semantic_refresh_required",),
                **base,
            )

        try:
            lineage = self.replacement.resolve(version.id, require_active=True)
        except ProjectionReplacementError:
            return self._blocked("promotion_lineage_invalid", **base)

        if lineage.previous_active_version_id is None:
            return SemanticLifecyclePlan(
                decision=SemanticLifecycleDecision.GENERATE,
                reasons=("no_previous_active_version",),
                **base,
            )

        source_screen = self.session.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.knowledge_version_id == lineage.previous_active_version_id,
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.canonical_id == screen.canonical_id,
            )
        )
        if source_screen is None:
            return SemanticLifecyclePlan(
                decision=SemanticLifecycleDecision.GENERATE,
                reasons=("source_screen_absent",),
                source_knowledge_version_id=lineage.previous_active_version_id,
                **base,
            )

        source_proposals = self.proposals.list(
            knowledge_version_id=lineage.previous_active_version_id,
            screen_knowledge_item_id=source_screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            limit=1000,
        )
        publishable = [
            proposal
            for proposal in source_proposals
            if proposal.current_review_status in self.PUBLISHABLE
        ]
        if not publishable:
            return SemanticLifecyclePlan(
                decision=SemanticLifecycleDecision.GENERATE,
                reasons=("no_publishable_source_semantic",),
                source_knowledge_version_id=lineage.previous_active_version_id,
                **base,
            )
        if len(publishable) != 1:
            return self._blocked(
                "ambiguous_publishable_source_semantics",
                source_knowledge_version_id=lineage.previous_active_version_id,
                **base,
            )

        source = publishable[0]
        source_base = self._source_base(source)
        try:
            effective_payload = self.effective.effective_payload(source.id)
        except (SemanticHistoryIntegrityError, SemanticProposalNotFoundError) as exc:
            return self._blocked(
                f"source_effective_payload_unavailable:{type(exc).__name__}",
                **base,
                **source_base,
            )

        if (
            effective_payload.get("semantic_type") != SemanticType.SCREEN_PURPOSE
            or effective_payload.get("screen_id") != screen.canonical_id
        ):
            return self._blocked(
                "source_effective_payload_identity_mismatch",
                **base,
                **source_base,
            )

        source_effective_content_hash = canonical_json_hash(effective_payload)
        source_base["source_effective_content_hash"] = source_effective_content_hash

        reasons: list[str] = []
        source_package = None
        try:
            source_package = self.evidence_builder.build(
                lineage.previous_active_version_id,
                source_screen.id,
            )
        except ScreenEvidenceError:
            reasons.append("source_evidence_unavailable")

        source_compatibility_hash = None
        if source_package is not None:
            source_compatibility_hash = semantic_evidence_compatibility_hash(source_package)
            source_base["source_compatibility_hash"] = source_compatibility_hash
            expected_payload = source_package.model_dump(mode="json", exclude={"evidence_hash"})
            if (
                source.evidence_hash != source_package.evidence_hash
                or list(source.evidence_ids) != list(source_package.evidence_ids)
                or source.evidence_payload != expected_payload
            ):
                reasons.append("source_semantic_stale")

        if source.prompt_version != PROMPT_VERSION:
            reasons.append("prompt_version_changed")
        if source.prompt_hash != PROMPT_HASH:
            reasons.append("prompt_hash_changed")
        if source.generation_model != model:
            reasons.append("generation_model_changed")
        if source.generation_parameters != GENERATION_PARAMETERS:
            reasons.append("generation_parameters_changed")
        if source.generation_parameters_hash != GENERATION_PARAMETERS_HASH:
            reasons.append("generation_parameters_hash_changed")

        if source_package is not None and "source_semantic_stale" not in reasons:
            if source_compatibility_hash != target_compatibility_hash:
                reasons.append("semantic_evidence_changed")

        if reasons:
            return SemanticLifecyclePlan(
                decision=SemanticLifecycleDecision.REINFERENCE_REQUIRED,
                reasons=tuple(reasons),
                **base,
                **source_base,
            )

        return SemanticLifecyclePlan(
            decision=SemanticLifecycleDecision.CARRY_FORWARD,
            reasons=("semantic_evidence_and_generation_contract_compatible",),
            **base,
            **source_base,
        )

    @staticmethod
    def _source_base(source: SemanticProposal) -> dict:
        return {
            "source_semantic_proposal_id": source.id,
            "source_knowledge_version_id": source.knowledge_version_id,
            "source_review_status": source.current_review_status,
            "source_review_revision": source.review_revision,
            "source_evidence_hash": source.evidence_hash,
        }

    @staticmethod
    def _blocked(*reasons: str, **fields) -> SemanticLifecyclePlan:
        return SemanticLifecyclePlan(
            decision=SemanticLifecycleDecision.BLOCKED,
            reasons=tuple(reasons),
            **fields,
        )

    @staticmethod
    def _uuid(value) -> uuid.UUID | None:
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return None
