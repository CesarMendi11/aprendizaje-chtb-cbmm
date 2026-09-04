from __future__ import annotations

import copy
from typing import Any

from erp_assistant.semantic.prompts.screen_purpose_v14 import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt_v14,
)
from erp_assistant.semantic.schemas import GeneratedScreenPurposeCandidate, ScreenEvidencePackage
from erp_assistant.semantic.schemas.screen_purpose_prompt_evidence_v14 import (
    ScreenPurposePromptEvidenceV14,
)
from erp_assistant.semantic.services.semantic_payloads import canonical_json_hash
from erp_assistant.structural.canonical.ids import content_hash
from erp_assistant.structural.canonical.privacy import contains_sensitive

from .errors import InferenceSensitiveContentError
from .ollama_structured_client import OllamaStructuredGenerationClient
from .screen_purpose_generation_v14 import (
    build_screen_purpose_generation_schema_v14,
    parse_generation_draft_v14,
)


class ScreenPurposeInferenceServiceV14:
    """Generate human-reviewable v14 claims without persisting semantic state."""

    def __init__(self, client: OllamaStructuredGenerationClient):
        self.client = client

    def generate(self, evidence_package: ScreenEvidencePackage) -> GeneratedScreenPurposeCandidate:
        package = ScreenEvidencePackage.model_validate(evidence_package.model_dump(mode="python"))
        prompt_evidence = ScreenPurposePromptEvidenceV14.from_package(package)
        self._validate_package_safety(prompt_evidence.model_dump(mode="json"))
        schema = build_screen_purpose_generation_schema_v14(package)
        response = self.client.generate(
            build_user_prompt_v14(prompt_evidence),
            system=SYSTEM_PROMPT,
            schema=schema,
            options={
                "temperature": GENERATION_PARAMETERS["temperature"],
                "num_predict": GENERATION_PARAMETERS["num_predict"],
                "num_ctx": GENERATION_PARAMETERS["num_ctx"],
            },
            think=GENERATION_PARAMETERS["think"],
        )
        normalization_warnings: list[str] = []
        inference = parse_generation_draft_v14(
            response.text,
            package=package,
            normalization_warnings=normalization_warnings,
        )
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
            warnings=[*package.warnings, *normalization_warnings],
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
