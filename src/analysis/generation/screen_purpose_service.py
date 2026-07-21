from __future__ import annotations

import copy
from typing import Any

from src.analysis.prompts import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from src.analysis.schemas import (
    GeneratedScreenPurposeCandidate,
    ScreenEvidencePackage,
    ScreenPurposeInference,
    ScreenPurposePromptEvidence,
)
from src.analysis.validators import parse_and_validate, validate_capability_grounding
from src.database.services.semantic_payloads import canonical_json_hash
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.privacy import contains_sensitive

from .errors import InferenceSensitiveContentError
from .ollama_structured_client import OllamaStructuredGenerationClient


class ScreenPurposeInferenceService:
    def __init__(self, client: OllamaStructuredGenerationClient):
        self.client = client

    def generate(self, evidence_package: ScreenEvidencePackage) -> GeneratedScreenPurposeCandidate:
        package = ScreenEvidencePackage.model_validate(evidence_package.model_dump(mode="python"))
        prompt_evidence = ScreenPurposePromptEvidence.from_package(package)
        self._validate_package_safety(prompt_evidence.model_dump(mode="json"))
        response = self.client.generate(
            build_user_prompt(prompt_evidence),
            system=SYSTEM_PROMPT,
            schema=ScreenPurposeInference.model_json_schema(),
        )
        inference = parse_and_validate(response.text, package)
        validate_capability_grounding(inference, package)
        inference_payload = inference.model_dump(mode="json")
        return GeneratedScreenPurposeCandidate(
            inference=inference,
            generation_model=self.client.settings.model,
            prompt_version=PROMPT_VERSION,
            prompt_hash=PROMPT_HASH,
            generation_parameters=copy.deepcopy(GENERATION_PARAMETERS),
            generation_parameters_hash=GENERATION_PARAMETERS_HASH,
            evidence_hash=package.evidence_hash,
            evidence_ids=list(package.evidence_ids),
            generated_content_hash=canonical_json_hash(inference_payload),
            structured_output_mode=response.mode,
            warnings=list(package.warnings),
            raw_response_hash=content_hash(response.text),
        )

    @classmethod
    def _validate_package_safety(cls, value: Any, *, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, item in value.items():
                normalized_key = str(child_key).casefold()
                if normalized_key in {"html", "selector", "password", "token"}:
                    raise InferenceSensitiveContentError(
                        "El paquete contiene propiedades no permitidas"
                    )
                cls._validate_package_safety(item, key=normalized_key)
        elif isinstance(value, list):
            for item in value:
                cls._validate_package_safety(item, key=key)
        elif isinstance(value, str):
            lowered = value.casefold()
            if "<script" in lowered or "javascript:" in lowered:
                raise InferenceSensitiveContentError("El paquete contiene texto sensible")
            identifier = (
                key.endswith("_id")
                or key.endswith("_ids")
                or key.endswith("_hash")
                or key in {"knowledge_version", "schema_version", "screen_route"}
            )
            if not identifier and contains_sensitive(value):
                raise InferenceSensitiveContentError("El paquete contiene texto sensible")
