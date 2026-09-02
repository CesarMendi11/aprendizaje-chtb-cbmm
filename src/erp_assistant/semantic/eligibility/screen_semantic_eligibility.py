from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from erp_assistant.semantic.schemas.screen_evidence import ScreenEvidencePackage
from erp_assistant.semantic.validators.screen_purpose_grounding_plan import build_grounding_plan

SemanticEligibilityStatus = Literal["eligible", "insufficient_evidence"]
SemanticEligibilityReason = Literal[
    "missing_primary_evidence",
    "missing_functional_structure",
    "missing_grounded_action_support",
]


class SemanticEligibilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SemanticEligibilityStatus
    eligible: bool
    reasons: tuple[SemanticEligibilityReason, ...]
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_evidence_count: int = Field(ge=0)
    functional_signal_count: int = Field(ge=0)


def evaluate_screen_semantic_eligibility(
    package: ScreenEvidencePackage,
) -> SemanticEligibilityAssessment:
    """Decide deterministically whether a safe screen package may reach the LLM."""
    functional_signal_count = (
        len(package.fields)
        + len(package.controls)
        + len(package.tables)
        + len(package.ui_states)
        + len(package.events)
        + len(package.transitions)
    )
    reasons: list[SemanticEligibilityReason] = []
    if not package.primary_evidence_ids:
        reasons.append("missing_primary_evidence")
    if functional_signal_count <= 0:
        reasons.append("missing_functional_structure")
    elif not build_grounding_plan(package).supported_actions:
        reasons.append("missing_grounded_action_support")
    return SemanticEligibilityAssessment(
        status="eligible" if not reasons else "insufficient_evidence",
        eligible=not reasons,
        reasons=tuple(reasons),
        evidence_hash=package.evidence_hash,
        primary_evidence_count=len(package.primary_evidence_ids),
        functional_signal_count=functional_signal_count,
    )
