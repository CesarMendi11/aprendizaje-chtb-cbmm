from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from src.analysis.generation.errors import (
    InferenceGroundingError,
    InferenceJSONError,
    InferenceReferenceError,
    InferenceSchemaError,
    InferenceScreenMismatchError,
    InferenceSensitiveContentError,
    InferenceUnsupportedActionError,
)
from src.analysis.schemas import CapabilityClaim, ScreenPurposeInference
from src.analysis.schemas.screen_purpose_grounding_plan import (
    RecognizedAction,
    ScreenPurposeGroundingPlan,
)
from src.analysis.schemas.screen_purpose_inference import InferenceModel, _safe_text
from src.analysis.validators.screen_purpose_grounding import validate_declared_capability


class GeneratedCapabilityDraft(InferenceModel):
    action: RecognizedAction
    statement: str
    evidence_refs: list[str] = Field(min_length=1, max_length=20)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: Any) -> str:
        return _safe_text(value, limit=400)

    @field_validator("evidence_refs")
    @classmethod
    def validate_refs(cls, values: list[str]) -> list[str]:
        return CapabilityClaim.validate_refs(values)


class ScreenPurposeGenerationDraft(InferenceModel):
    semantic_type: Literal["screen_purpose"]
    screen_id: str
    supported_capabilities: list[GeneratedCapabilityDraft] = Field(
        min_length=1, max_length=12
    )
    limitations: list[str] = Field(default_factory=list, max_length=0)
    uncertainties: list[str] = Field(default_factory=list, max_length=0)

    @field_validator("screen_id")
    @classmethod
    def validate_screen_id(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240:
            raise ValueError("screen_id inválido")
        return value.strip()

def build_screen_purpose_generation_schema(
    grounding_plan: ScreenPurposeGroundingPlan,
    *,
    screen_id: str,
) -> dict[str, Any]:
    """Build the deterministic Ollama contract from the grounding plan."""
    alternatives = []
    for hint in grounding_plan.supported_actions:
        alternatives.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "statement", "evidence_refs"],
                "properties": {
                    "action": {"const": hint.action},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 400},
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "uniqueItems": True,
                        "items": {"enum": list(hint.evidence_refs)},
                    },
                },
            }
        )
    if not alternatives:
        raise InferenceGroundingError(
            "El plan no contiene acciones generativas compatibles",
            stage="grounding_plan_validation",
            category="no_supported_generation_actions",
        )
    empty_list = {"type": "array", "maxItems": 0}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "semantic_type",
            "screen_id",
            "supported_capabilities",
            "limitations",
            "uncertainties",
        ],
        "properties": {
            "semantic_type": {"const": "screen_purpose"},
            "screen_id": {"const": screen_id},
            "supported_capabilities": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {"oneOf": alternatives},
            },
            "limitations": empty_list,
            "uncertainties": dict(empty_list),
        },
    }


def parse_generation_draft(
    raw: str,
    *,
    screen_id: str,
    screen_title: str,
    grounding_plan: ScreenPurposeGroundingPlan,
) -> ScreenPurposeInference:
    if raw.lstrip().startswith("```") or raw.rstrip().endswith("```"):
        raise InferenceJSONError("La inferencia no es JSON puro")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InferenceJSONError("La inferencia contiene JSON inválido") from exc
    if not isinstance(value, dict):
        raise InferenceJSONError("La raíz de la inferencia debe ser un objeto")
    if value.get("supported_capabilities") == []:
        raise InferenceGroundingError(
            "No se generaron capabilities respaldadas",
            stage="grounding_validation",
            location=("supported_capabilities",),
            category="no_supported_capabilities_generated",
        )
    try:
        draft = ScreenPurposeGenerationDraft.model_validate(value)
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False, include_input=True)[0]
        rejected = first.get("input")
        diagnostic = {
            "stage": "pydantic_validation",
            "location": first.get("loc", ()),
            "category": str(first.get("type", "schema")),
            "value_length": len(rejected) if isinstance(rejected, (str, list, dict)) else None,
            "value_type": type(rejected).__name__,
        }
        error = (
            InferenceSensitiveContentError
            if diagnostic["category"] in {"inference_privacy", "inference_prompt_injection"}
            else InferenceSchemaError
        )
        raise error("La inferencia no cumple el draft generativo", **diagnostic) from exc
    if draft.semantic_type != "screen_purpose":
        raise InferenceSchemaError("semantic_type no corresponde al contrato generativo")
    if draft.screen_id != screen_id:
        raise InferenceScreenMismatchError("La inferencia corresponde a otra pantalla")
    hints = {hint.action: hint for hint in grounding_plan.supported_actions}
    claims = []
    for position, capability in enumerate(draft.supported_capabilities):
        hint = hints.get(capability.action)
        if hint is None:
            raise InferenceUnsupportedActionError(
                "La acción declarada no pertenece al plan",
                stage="grounding_validation",
                location=("supported_capabilities", position, "action"),
                category="declared_action_not_supported",
            )
        if not set(capability.evidence_refs).issubset(hint.evidence_refs):
            raise InferenceReferenceError(
                "Las referencias no corresponden a la acción declarada",
                stage="grounding_validation",
                location=("supported_capabilities", position, "evidence_refs"),
                category="declared_action_reference_not_permitted",
            )
        validate_declared_capability(capability, hint, position=position)
        claims.append(
            CapabilityClaim(
                statement=capability.statement,
                evidence_refs=list(capability.evidence_refs),
            )
        )
    purpose_summary = build_deterministic_purpose_summary(
        screen_title=screen_title,
        capabilities=list(draft.supported_capabilities),
        grounding_plan=grounding_plan,
    )
    return ScreenPurposeInference(
        semantic_type="screen_purpose",
        screen_id=draft.screen_id,
        purpose_summary=purpose_summary,
        supported_capabilities=claims,
        limitations=[],
        uncertainties=[],
    )


ACTION_ORDER = ("search", "view", "navigate", "create", "edit", "delete", "process")
DIRECT_VERBS = {
    "search": "buscar",
    "view": "visualizar",
    "create": "crear",
    "edit": "editar",
    "delete": "eliminar",
    "process": "procesar",
}


def build_deterministic_purpose_summary(
    *,
    screen_title: str,
    capabilities: list[GeneratedCapabilityDraft],
    grounding_plan: ScreenPurposeGroundingPlan,
) -> str:
    """Build a stable summary solely from validated actions and safe metadata."""
    if not capabilities:
        raise InferenceGroundingError(
            "No se puede construir un resumen sin capabilities",
            stage="grounding_validation",
            category="no_supported_capabilities_generated",
        )
    try:
        title = _safe_text(screen_title, limit=240)
    except Exception as exc:  # Pydantic core error at the generation trust boundary.
        raise InferenceSensitiveContentError(
            "El título no es seguro para construir el resumen",
            stage="deterministic_summary",
            category="unsafe_screen_title",
        ) from exc
    subject = title[:1].lower() + title[1:]
    hints = {hint.action: hint for hint in grounding_plan.supported_actions}
    actions = {capability.action for capability in capabilities}
    ordered = [action for action in ACTION_ORDER if action in actions]
    direct = [
        action
        for action in ordered
        if hints[action].narrative_rule == "direct_allowed"
    ]
    prudent = [
        action for action in ordered if hints[action].narrative_rule == "prudent_only"
    ]
    sentences = []
    verbs = [DIRECT_VERBS[action] for action in direct if action != "navigate"]
    if "search" in direct and "view" in direct:
        verbs[verbs.index("visualizar")] = "consultar"
    if verbs:
        body = _join_spanish(verbs)
        sentence = f"Permite {body} {subject}"
        if len(verbs) == 1:
            sentence += " desde la pantalla"
        if "navigate" in direct:
            sentence += ", así como navegar entre las páginas de resultados"
        sentences.append(sentence + ".")
    elif "navigate" in direct:
        sentences.append(
            f"Permite navegar entre las páginas de resultados de {subject}."
        )
    for action in prudent:
        sentences.append(
            f"La pantalla {title} presenta una opción relacionada con "
            f"{DIRECT_VERBS[action]}."
        )
    summary = " ".join(sentences)
    return _safe_text(summary, limit=600)


def _join_spanish(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " y " + values[-1]
