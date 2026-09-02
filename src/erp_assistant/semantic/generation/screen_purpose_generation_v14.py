from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from erp_assistant.semantic.generation.errors import (
    InferenceGroundingError,
    InferenceJSONError,
    InferenceSchemaError,
    InferenceScreenMismatchError,
    InferenceSensitiveContentError,
)
from erp_assistant.semantic.schemas import ScreenEvidencePackage, ScreenPurposeInference
from erp_assistant.semantic.validators.screen_purpose_claim_policy import (
    claimable_reference_ids,
    validate_v14_claim_references,
)


def build_screen_purpose_generation_schema_v14(
    package: ScreenEvidencePackage,
) -> dict[str, Any]:
    references = list(claimable_reference_ids(package))
    if not references:
        raise InferenceGroundingError(
            "La pantalla no contiene evidencia estructural apta para propuestas funcionales",
            stage="claim_reference_validation",
            category="no_claimable_semantic_evidence",
        )

    claim_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement", "evidence_refs"],
        "properties": {
            "statement": {
                "type": "string",
                "minLength": 3,
                "maxLength": 400,
            },
            "evidence_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "uniqueItems": True,
                "items": {"type": "string", "enum": references},
            },
        },
    }
    bounded_text = {"type": "string", "minLength": 2, "maxLength": 300}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "semantic_type",
            "screen_id",
            "purpose_summary",
            "supported_capabilities",
            "limitations",
            "uncertainties",
        ],
        "properties": {
            "semantic_type": {"const": "screen_purpose"},
            "screen_id": {"const": package.screen_id},
            "purpose_summary": {
                "type": "string",
                "minLength": 4,
                "maxLength": 600,
            },
            "supported_capabilities": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": claim_schema,
            },
            "limitations": {
                "type": "array",
                "maxItems": 8,
                "items": bounded_text,
            },
            "uncertainties": {
                "type": "array",
                "maxItems": 8,
                "items": bounded_text,
            },
        },
    }


def parse_generation_draft_v14(
    raw: str,
    *,
    package: ScreenEvidencePackage,
) -> ScreenPurposeInference:
    if raw.lstrip().startswith("```") or raw.rstrip().endswith("```"):
        raise InferenceJSONError("La inferencia no es JSON puro")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InferenceJSONError("La inferencia contiene JSON inválido") from exc
    if not isinstance(value, dict):
        raise InferenceJSONError("La raíz de la inferencia debe ser un objeto")

    try:
        inference = ScreenPurposeInference.model_validate(value)
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False, include_input=True)[0]
        error_type = str(first.get("type", "schema"))
        rejected = first.get("input")
        diagnostic = {
            "stage": "pydantic_validation",
            "location": first.get("loc", ()),
            "category": error_type,
            "value_length": len(rejected) if isinstance(rejected, (str, list, dict)) else None,
            "value_type": type(rejected).__name__,
        }
        if error_type in {"inference_privacy", "inference_prompt_injection"}:
            raise InferenceSensitiveContentError(
                "La inferencia contiene texto no permitido", **diagnostic
            ) from exc
        raise InferenceSchemaError("La inferencia no cumple el esquema", **diagnostic) from exc

    if inference.screen_id != package.screen_id:
        raise InferenceScreenMismatchError("La inferencia corresponde a otra pantalla")

    validate_v14_claim_references(inference, package)
    return inference
