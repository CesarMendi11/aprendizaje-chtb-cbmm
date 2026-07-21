from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from src.database.enums import ReviewActionType
from src.database.models import SemanticProposal, SemanticReviewAction
from src.database.repositories import (
    SemanticProposalRepository,
    SemanticReviewActionRepository,
)
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import sanitize_text

from .semantic_effective_payload_service import SemanticEffectivePayloadService
from .semantic_exceptions import (
    SemanticPayloadError,
    SemanticProposalNotFoundError,
    SemanticRevisionConflictError,
    SemanticSensitiveContentError,
    SemanticTransitionError,
)
from .semantic_payloads import (
    canonical_json_hash,
    semantic_review_action_payload,
    validate_semantic_payload,
)

ALLOWED_SOURCES = {"cli", "admin_api", "review_panel", "migration"}
TRANSITIONS = {
    (ReviewStatus.PENDING_REVIEW, ReviewActionType.APPROVE): ReviewStatus.APPROVED,
    (ReviewStatus.PENDING_REVIEW, ReviewActionType.REJECT): ReviewStatus.REJECTED,
    (ReviewStatus.PENDING_REVIEW, ReviewActionType.CORRECT): ReviewStatus.CORRECTED,
    (ReviewStatus.APPROVED, ReviewActionType.RESET_TO_PENDING): ReviewStatus.PENDING_REVIEW,
    (ReviewStatus.REJECTED, ReviewActionType.RESET_TO_PENDING): ReviewStatus.PENDING_REVIEW,
    (ReviewStatus.CORRECTED, ReviewActionType.RESET_TO_PENDING): ReviewStatus.PENDING_REVIEW,
}


class SemanticReviewService:
    def __init__(self, session: Session):
        self.session = session
        self.proposals = SemanticProposalRepository(session)
        self.actions = SemanticReviewActionRepository(session)
        self.effective = SemanticEffectivePayloadService(session)

    def approve(
        self,
        proposal_id,
        *,
        expected_revision: int,
        reviewer_subject: str,
        source: str,
        review_notes: str | None = None,
    ) -> SemanticProposal:
        return self._transition(
            proposal_id,
            action=ReviewActionType.APPROVE,
            expected_revision=expected_revision,
            reviewer_subject=reviewer_subject,
            source=source,
            review_notes=review_notes,
        )

    def reject(
        self,
        proposal_id,
        *,
        expected_revision: int,
        reviewer_subject: str,
        source: str,
        review_notes: str | None = None,
    ) -> SemanticProposal:
        return self._transition(
            proposal_id,
            action=ReviewActionType.REJECT,
            expected_revision=expected_revision,
            reviewer_subject=reviewer_subject,
            source=source,
            review_notes=review_notes,
        )

    def correct(
        self,
        proposal_id,
        corrected_payload,
        *,
        expected_revision: int,
        reviewer_subject: str,
        source: str,
        review_notes: str | None = None,
    ) -> SemanticProposal:
        payload = validate_semantic_payload(
            corrected_payload,
            field="corrected_payload",
            require_purpose_summary=True,
        )
        return self._transition(
            proposal_id,
            action=ReviewActionType.CORRECT,
            expected_revision=expected_revision,
            reviewer_subject=reviewer_subject,
            source=source,
            review_notes=review_notes,
            corrected_payload=payload,
        )

    def reset_to_pending(
        self,
        proposal_id,
        *,
        expected_revision: int,
        reviewer_subject: str,
        source: str,
        review_notes: str | None = None,
    ) -> SemanticProposal:
        return self._transition(
            proposal_id,
            action=ReviewActionType.RESET_TO_PENDING,
            expected_revision=expected_revision,
            reviewer_subject=reviewer_subject,
            source=source,
            review_notes=review_notes,
        )

    def history(self, proposal_id) -> list[dict]:
        self._get(proposal_id)
        return [
            semantic_review_action_payload(action)
            for action in self.actions.list_for_proposal(proposal_id)
        ]

    def effective_payload(self, proposal_id) -> dict:
        return self.effective.effective_payload(proposal_id)

    def publishable_payload(self, proposal_id) -> dict | None:
        return self.effective.publishable_payload(proposal_id)

    def _transition(
        self,
        proposal_id,
        *,
        action: ReviewActionType,
        expected_revision: int,
        reviewer_subject: str,
        source: str,
        review_notes: str | None,
        corrected_payload: dict | None = None,
    ) -> SemanticProposal:
        reviewer = self._actor(reviewer_subject)
        action_source = self._source(source)
        notes = self._notes(review_notes)
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise SemanticRevisionConflictError("expected_revision es obligatorio y no negativo")
        proposal = self.proposals.lock_for_update(proposal_id)
        if proposal is None:
            raise SemanticProposalNotFoundError("SemanticProposal no encontrada")
        if proposal.review_revision != expected_revision:
            raise SemanticRevisionConflictError("La revisión semántica está desactualizada")
        new_status = TRANSITIONS.get((proposal.current_review_status, action))
        if new_status is None:
            raise SemanticTransitionError(
                f"Transición no permitida: {proposal.current_review_status} mediante {action}"
            )
        effective_before = self.effective.effective_payload(proposal.id)
        if action == ReviewActionType.CORRECT:
            if corrected_payload is None:
                raise SemanticPayloadError("corrected_payload es obligatorio")
            if canonical_json_hash(corrected_payload) in {
                canonical_json_hash(proposal.source_payload),
                canonical_json_hash(effective_before),
            }:
                raise SemanticPayloadError("La corrección debe cambiar el contenido efectivo")
            content_for_hash = corrected_payload
        else:
            content_for_hash = effective_before
        self.actions.append(
            SemanticReviewAction(
                semantic_proposal_id=proposal.id,
                action=action,
                previous_status=proposal.current_review_status,
                new_status=new_status,
                corrected_payload=copy.deepcopy(corrected_payload),
                review_notes=notes,
                reviewer_subject=reviewer,
                proposal_content_hash=canonical_json_hash(content_for_hash),
                source=action_source,
            )
        )
        proposal.current_review_status = new_status
        proposal.review_revision += 1
        self.session.flush()
        return proposal

    def _get(self, proposal_id) -> SemanticProposal:
        proposal = self.proposals.get_by_id(proposal_id)
        if proposal is None:
            raise SemanticProposalNotFoundError("SemanticProposal no encontrada")
        return proposal

    @staticmethod
    def _actor(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SemanticPayloadError("reviewer_subject es obligatorio")
        clean, detections = sanitize_text(value, 241)
        if detections:
            raise SemanticSensitiveContentError("reviewer_subject contiene datos sensibles")
        if not clean or len(clean) > 240:
            raise SemanticPayloadError("reviewer_subject es inválido")
        return clean

    @staticmethod
    def _source(value: str) -> str:
        source = str(value or "").strip()
        if source not in ALLOWED_SOURCES:
            raise SemanticPayloadError("source no está permitido")
        return source

    @staticmethod
    def _notes(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise SemanticPayloadError("review_notes debe ser texto")
        clean, detections = sanitize_text(value, 4001)
        if detections:
            raise SemanticSensitiveContentError("review_notes contiene datos sensibles")
        if len(clean) > 4000:
            raise SemanticPayloadError("review_notes excede el tamaño permitido")
        return clean or None
