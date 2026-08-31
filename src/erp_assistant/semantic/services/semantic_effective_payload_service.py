from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import SemanticLifecycleOrigin
from erp_assistant.persistence.postgres.models import SemanticProposal
from erp_assistant.persistence.postgres.repositories import (
    SemanticProposalRepository,
    SemanticReviewActionRepository,
)
from erp_assistant.structural.canonical.enums import ReviewStatus

from .semantic_exceptions import (
    SemanticHistoryIntegrityError,
    SemanticProposalNotFoundError,
)
from .semantic_payloads import canonical_json_hash, semantic_review_action_payload


class SemanticEffectivePayloadService:
    def __init__(self, session: Session):
        self.proposals = SemanticProposalRepository(session)
        self.actions = SemanticReviewActionRepository(session)

    def effective_payload(self, proposal_id) -> dict:
        proposal = self._get(proposal_id)
        if proposal.current_review_status == ReviewStatus.CORRECTED:
            correction = self.actions.latest_correction_after_last_reset(proposal.id)
            if correction is not None and correction.corrected_payload is not None:
                return copy.deepcopy(correction.corrected_payload)
            if proposal.lifecycle_origin == SemanticLifecycleOrigin.CARRIED_FORWARD:
                payload = copy.deepcopy(proposal.source_payload)
                if (
                    proposal.source_effective_content_hash is None
                    or canonical_json_hash(payload)
                    != proposal.source_effective_content_hash
                ):
                    raise SemanticHistoryIntegrityError(
                        "El carry-forward corrected no coincide con el payload efectivo source"
                    )
                return payload
            raise SemanticHistoryIntegrityError(
                "La propuesta corrected no tiene una corrección efectiva consistente"
            )
        return copy.deepcopy(proposal.source_payload)

    def publishable_payload(self, proposal_id) -> dict | None:
        proposal = self._get(proposal_id)
        if proposal.current_review_status not in {
            ReviewStatus.APPROVED,
            ReviewStatus.CORRECTED,
        }:
            return None
        return self.effective_payload(proposal.id)

    def describe(self, proposal_id) -> dict:
        proposal = self._get(proposal_id)
        effective = self.effective_payload(proposal.id)
        publishable = proposal.current_review_status in {
            ReviewStatus.APPROVED,
            ReviewStatus.CORRECTED,
        }
        return {
            "id": str(proposal.id),
            "semantic_id": proposal.semantic_id,
            "knowledge_version_id": str(proposal.knowledge_version_id),
            "screen_knowledge_item_id": str(proposal.screen_knowledge_item_id),
            "semantic_type": str(proposal.semantic_type),
            "current_review_status": str(proposal.current_review_status),
            "review_revision": proposal.review_revision,
            "generation_model": proposal.generation_model,
            "prompt_version": proposal.prompt_version,
            "source_content_hash": proposal.source_content_hash,
            "evidence_hash": proposal.evidence_hash,
            "prompt_hash": proposal.prompt_hash,
            "generation_parameters_hash": proposal.generation_parameters_hash,
            "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
            "updated_at": proposal.updated_at.isoformat() if proposal.updated_at else None,
            "source_payload": copy.deepcopy(proposal.source_payload),
            "effective_payload": effective,
            "publishable": publishable,
            "evidence_ids": list(proposal.evidence_ids),
            "history": [
                semantic_review_action_payload(action)
                for action in self.actions.list_for_proposal(proposal.id)
            ],
        }

    def _get(self, proposal_id) -> SemanticProposal:
        proposal = self.proposals.get_by_id(proposal_id)
        if proposal is None:
            raise SemanticProposalNotFoundError("SemanticProposal no encontrada")
        return proposal
