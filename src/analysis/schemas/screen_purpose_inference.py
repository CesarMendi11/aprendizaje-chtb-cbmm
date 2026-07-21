from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from src.knowledge.canonical.privacy import sanitize_text

INJECTION_PHRASES = (
    "ignore previous instructions",
    "ignora las instrucciones",
    "system prompt",
    "act as system",
    "cambia tu rol",
)


class InferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _safe_text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PydanticCustomError("inference_empty", "El texto inferido no puede estar vacío")
    normalized = " ".join(value.split())
    if len(normalized) > limit:
        raise PydanticCustomError("inference_length", "El texto inferido excede el límite")
    clean, detections = sanitize_text(normalized, limit + 1)
    if detections:
        raise PydanticCustomError("inference_privacy", "El texto inferido no es seguro")
    if not clean:
        raise PydanticCustomError("inference_empty", "El texto inferido no puede estar vacío")
    lowered = clean.casefold()
    if any(phrase in lowered for phrase in INJECTION_PHRASES):
        raise PydanticCustomError(
            "inference_prompt_injection",
            "El texto inferido contiene instrucciones no permitidas",
        )
    return clean


class CapabilityClaim(InferenceModel):
    statement: str
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: Any) -> str:
        return _safe_text(value, limit=400)

    @field_validator("evidence_refs")
    @classmethod
    def validate_refs(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
                raise ValueError("Referencia estructural inválida")
            normalized.append(value.strip())
        if len(set(normalized)) != len(normalized):
            raise ValueError("Referencias estructurales duplicadas")
        return normalized


class ScreenPurposeInference(InferenceModel):
    semantic_type: Literal["screen_purpose"]
    screen_id: str
    purpose_summary: str
    supported_capabilities: list[CapabilityClaim] = Field(max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("screen_id")
    @classmethod
    def validate_screen_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
            raise ValueError("screen_id inválido")
        return value.strip()

    @field_validator("purpose_summary")
    @classmethod
    def validate_summary(cls, value: Any) -> str:
        return _safe_text(value, limit=600)

    @field_validator("limitations", "uncertainties")
    @classmethod
    def validate_text_lists(cls, values: list[str]) -> list[str]:
        return [_safe_text(value, limit=300) for value in values]


class GeneratedScreenPurposeCandidate(InferenceModel):
    inference: ScreenPurposeInference
    generation_model: str
    prompt_version: str
    prompt_hash: str
    generation_parameters: dict[str, Any]
    generation_parameters_hash: str
    evidence_hash: str
    evidence_ids: list[str]
    generated_content_hash: str
    structured_output_mode: Literal["json_schema", "json"]
    warnings: list[str]
    raw_response_hash: str | None = None
