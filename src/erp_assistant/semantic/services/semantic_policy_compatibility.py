from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from erp_assistant.semantic.generation.errors import ScreenPurposeGenerationError
from erp_assistant.semantic.schemas import ScreenEvidencePackage, ScreenPurposeInference
from erp_assistant.semantic.validators.screen_purpose_claim_policy import (
    validate_v14_claim_references,
)


@dataclass(frozen=True)
class SemanticPolicyCompatibility:
    compatible: bool
    reason: str | None = None
    category: str | None = None


def assess_screen_purpose_policy_compatibility(
    payload,
    package: ScreenEvidencePackage,
) -> SemanticPolicyCompatibility:
    """Revalidate an effective semantic payload against the current grounding policy.

    Historical prompt identity is provenance, not current authorization. A reviewed
    payload remains usable only while its claims still point to currently claimable
    structural evidence. Semantic truth remains a human-review responsibility.
    """
    if not isinstance(payload, dict):
        return SemanticPolicyCompatibility(False, "invalid_effective_payload")

    try:
        inference = ScreenPurposeInference.model_validate(payload)
    except ValidationError:
        return SemanticPolicyCompatibility(False, "invalid_effective_payload")

    if inference.screen_id != package.screen_id:
        return SemanticPolicyCompatibility(False, "effective_screen_mismatch")

    try:
        validate_v14_claim_references(inference, package)
    except ScreenPurposeGenerationError as exc:
        return SemanticPolicyCompatibility(
            False,
            "current_grounding_incompatible",
            exc.category,
        )

    return SemanticPolicyCompatibility(True)
