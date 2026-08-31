from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from erp_assistant.persistence.postgres.enums import SemanticLifecycleOrigin
from erp_assistant.persistence.postgres.models import SemanticProposal
from erp_assistant.semantic.schemas import ScreenPurposeInference
from erp_assistant.semantic.services.semantic_payloads import canonical_json_hash


class ScreenSemanticState(StrEnum):
    NO_PROPOSAL = "no_proposal"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SemanticProjection:
    state: ScreenSemanticState
    active: SemanticProposal | None
    payload: ScreenPurposeInference | None
    diagnostic: str | None = None


def parse_screen_purpose_payload(value) -> ScreenPurposeInference | None:
    try:
        return ScreenPurposeInference.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        return None


def semantic_projection(
    rows: tuple[tuple[SemanticProposal, int], ...],
    actions: dict | None = None,
) -> SemanticProjection:
    if not rows:
        return SemanticProjection(ScreenSemanticState.NO_PROPOSAL, None, None)
    latest = rows[-1][0]
    tied = [proposal for proposal, _ in rows if proposal.created_at == latest.created_at]
    if len({(str(p.semantic_type), str(p.current_review_status)) for p in tied}) > 1:
        return SemanticProjection(
            ScreenSemanticState.MIXED,
            latest,
            None,
            "Existen propuestas vigentes incompatibles con la misma prioridad.",
        )
    payload_value = latest.source_payload
    if str(latest.current_review_status) == "corrected":
        proposal_actions = (actions or {}).get(latest.id, ())
        correction = None
        for action in reversed(proposal_actions):
            if str(action.action) == "reset_to_pending":
                break
            if str(action.action) == "correct" and action.corrected_payload is not None:
                correction = parse_screen_purpose_payload(action.corrected_payload)
                if correction is None:
                    return SemanticProjection(
                        ScreenSemanticState.UNAVAILABLE,
                        latest,
                        None,
                        "La corrección semántica persistida no cumple el esquema esperado.",
                    )
                break
        if correction is None:
            if latest.lifecycle_origin == SemanticLifecycleOrigin.CARRIED_FORWARD:
                carried_payload = parse_screen_purpose_payload(latest.source_payload)
                if carried_payload is None:
                    return SemanticProjection(
                        ScreenSemanticState.UNAVAILABLE,
                        latest,
                        None,
                        "El payload heredado por carry-forward no cumple el esquema esperado.",
                    )
                carried_hash = canonical_json_hash(carried_payload.model_dump(mode="json"))
                if (
                    latest.source_effective_content_hash is None
                    or carried_hash != latest.source_effective_content_hash
                ):
                    return SemanticProjection(
                        ScreenSemanticState.UNAVAILABLE,
                        latest,
                        None,
                        "El payload heredado por carry-forward no coincide con su provenance.",
                    )
                return SemanticProjection(
                    ScreenSemanticState.CORRECTED,
                    latest,
                    carried_payload,
                )
            return SemanticProjection(
                ScreenSemanticState.UNAVAILABLE,
                latest,
                None,
                "La propuesta corregida no tiene una acción de corrección válida.",
            )
        return SemanticProjection(ScreenSemanticState.CORRECTED, latest, correction)
    payload = parse_screen_purpose_payload(payload_value)
    if payload is None:
        return SemanticProjection(
            ScreenSemanticState.UNAVAILABLE,
            latest,
            None,
            "El payload semántico persistido no cumple el esquema esperado.",
        )
    return SemanticProjection(
        ScreenSemanticState(str(latest.current_review_status)),
        latest,
        payload,
    )
