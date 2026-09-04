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
from erp_assistant.structural.canonical.ids import normalize_text


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


def _normalize_duplicate_functional_claims(value: dict[str, Any]) -> int:
    capabilities = value.get("supported_capabilities")
    if not isinstance(capabilities, list):
        return 0

    removed = 0
    deduplicated: list[Any] = []
    positions: dict[str, int] = {}

    for claim in capabilities:
        if not isinstance(claim, dict):
            deduplicated.append(claim)
            continue

        statement = claim.get("statement")
        if not isinstance(statement, str):
            deduplicated.append(claim)
            continue

        statement_key = normalize_text(statement)
        if not statement_key:
            deduplicated.append(claim)
            continue

        if statement_key not in positions:
            positions[statement_key] = len(deduplicated)
            deduplicated.append(claim)
            continue

        removed += 1
        existing = deduplicated[positions[statement_key]]
        existing_refs = existing.get("evidence_refs")
        incoming_refs = claim.get("evidence_refs")

        if isinstance(existing_refs, list) and isinstance(incoming_refs, list):
            for reference in incoming_refs:
                if reference not in existing_refs:
                    existing_refs.append(reference)

    value["supported_capabilities"] = deduplicated
    return removed


def _normalize_duplicate_evidence_refs(value: dict[str, Any]) -> int:
    capabilities = value.get("supported_capabilities")
    if not isinstance(capabilities, list):
        return 0

    removed = 0
    for claim in capabilities:
        if not isinstance(claim, dict):
            continue
        references = claim.get("evidence_refs")
        if not isinstance(references, list):
            continue
        deduplicated: list[Any] = []
        seen: set[Any] = set()
        for reference in references:
            try:
                already_seen = reference in seen
            except TypeError:
                deduplicated.append(reference)
                continue
            if already_seen:
                removed += 1
                continue
            seen.add(reference)
            deduplicated.append(reference)
        claim["evidence_refs"] = deduplicated
    return removed


def parse_generation_draft_v14(
    raw: str,
    *,
    package: ScreenEvidencePackage,
    normalization_warnings: list[str] | None = None,
) -> ScreenPurposeInference:
    if raw.lstrip().startswith("```") or raw.rstrip().endswith("```"):
        raise InferenceJSONError("La inferencia no es JSON puro")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InferenceJSONError("La inferencia contiene JSON inválido") from exc
    if not isinstance(value, dict):
        raise InferenceJSONError("La raíz de la inferencia debe ser un objeto")

    removed_claims = _normalize_duplicate_functional_claims(value)
    if removed_claims and normalization_warnings is not None:
        normalization_warnings.append(f"deduplicated_functional_claims:{removed_claims}")

    removed_references = _normalize_duplicate_evidence_refs(value)
    if removed_references and normalization_warnings is not None:
        normalization_warnings.append(f"deduplicated_evidence_refs:{removed_references}")

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
