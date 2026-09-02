from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from erp_assistant.semantic.generation.errors import ScreenPurposeGenerationError
from erp_assistant.semantic.schemas import ScreenEvidencePackage, ScreenPurposeInference
from erp_assistant.semantic.validators import (
    allowed_references,
    build_grounding_plan,
    validate_capability_grounding,
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
    payload remains usable only while its effective claims still validate against
    the current Safe Evidence and Grounding Plan.
    """
    if not isinstance(payload, dict):
        return SemanticPolicyCompatibility(False, "invalid_effective_payload")

    try:
        inference = ScreenPurposeInference.model_validate(payload)
    except ValidationError:
        return SemanticPolicyCompatibility(False, "invalid_effective_payload")

    if inference.screen_id != package.screen_id:
        return SemanticPolicyCompatibility(False, "effective_screen_mismatch")

    allowed = allowed_references(package)
    if any(
        reference not in allowed
        for claim in inference.supported_capabilities
        for reference in claim.evidence_refs
    ):
        return SemanticPolicyCompatibility(False, "unknown_effective_reference")

    try:
        validate_capability_grounding(
            inference,
            package,
            build_grounding_plan(package),
        )
    except ScreenPurposeGenerationError as exc:
        return SemanticPolicyCompatibility(
            False,
            "current_grounding_incompatible",
            exc.category,
        )

    return SemanticPolicyCompatibility(True)
