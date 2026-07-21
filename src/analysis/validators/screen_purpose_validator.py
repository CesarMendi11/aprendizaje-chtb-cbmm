from __future__ import annotations

import json

from pydantic import ValidationError

from src.analysis.generation.errors import (
    InferenceJSONError,
    InferenceReferenceError,
    InferenceSchemaError,
    InferenceScreenMismatchError,
    InferenceSensitiveContentError,
)
from src.analysis.schemas import ScreenEvidencePackage, ScreenPurposeInference


def allowed_references(package: ScreenEvidencePackage) -> set[str]:
    values = {package.screen_id, package.module.module_id, *package.evidence_ids}
    values.update(field.field_id for field in package.fields)
    values.update(control.control_id for control in package.controls)
    values.update(table.table_id for table in package.tables)
    values.update(column.column_id for table in package.tables for column in table.columns)
    values.update(state.state_id for state in package.ui_states)
    values.update(event.event_id for event in package.events)
    values.update(transition.transition_id for transition in package.transitions)
    return values


def parse_and_validate(raw: str, package: ScreenEvidencePackage) -> ScreenPurposeInference:
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
        errors = exc.errors(include_url=False, include_context=False, include_input=True)
        first = errors[0]
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
    allowed = allowed_references(package)
    unknown = {
        reference
        for claim in inference.supported_capabilities
        for reference in claim.evidence_refs
        if reference not in allowed
    }
    if unknown:
        raise InferenceReferenceError("La inferencia contiene referencias desconocidas")
    return inference
