from __future__ import annotations

import json
from types import SimpleNamespace

from erp_assistant.semantic.generation.ollama_structured_client import StructuredGenerationResponse
from erp_assistant.semantic.generation.screen_purpose_service_v14 import (
    ScreenPurposeInferenceServiceV14,
)
from erp_assistant.semantic.prompts.screen_purpose_v14 import (
    GENERATION_PARAMETERS,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from tests.semantic.test_screen_purpose_v14_design import package, valid_output


class CapturingClient:
    def __init__(self):
        self.settings = SimpleNamespace(model="v14-test-model")
        self.calls = []

    def generate(self, prompt, *, system, schema, options=None, think=None):
        self.calls.append(
            {
                "prompt": prompt,
                "system": system,
                "schema": schema,
                "options": options,
                "think": think,
            }
        )
        return StructuredGenerationResponse(
            json.dumps(valid_output(), ensure_ascii=False),
            "json_schema",
        )


class DuplicateReferenceClient(CapturingClient):
    def generate(self, prompt, *, system, schema, options=None, think=None):
        value = valid_output()
        value["supported_capabilities"][0]["evidence_refs"] = [
            "column:owner",
            "column:risk",
            "column:owner",
        ]
        return StructuredGenerationResponse(
            json.dumps(value, ensure_ascii=False),
            "json_schema",
        )


class DuplicateClaimClient(CapturingClient):
    def generate(self, prompt, *, system, schema, options=None, think=None):
        value = valid_output()

        value["supported_capabilities"].append(
            {
                "statement": value["supported_capabilities"][0]["statement"],
                "evidence_refs": ["column:owner"],
            }
        )

        return StructuredGenerationResponse(
            json.dumps(value, ensure_ascii=False),
            "json_schema",
        )


def test_v14_service_uses_rich_contract_and_2048_budget_without_persistence():
    client = CapturingClient()
    generated = ScreenPurposeInferenceServiceV14(client).generate(package())

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["options"] == {
        "temperature": GENERATION_PARAMETERS["temperature"],
        "num_predict": 2048,
        "num_ctx": 8192,
    }
    assert call["think"] is False
    assert "supported_actions" not in call["prompt"]
    assert "grounding_plan" not in call["prompt"]
    assert "column:risk" in call["prompt"]
    assert "column:risk" in json.dumps(call["schema"], ensure_ascii=False)

    assert generated.generation_model == "v14-test-model"
    assert generated.prompt_version == PROMPT_VERSION
    assert generated.prompt_hash == PROMPT_HASH
    assert generated.generation_parameters == GENERATION_PARAMETERS
    assert generated.inference.supported_capabilities[0].statement.startswith("Presenta datos")
    assert generated.raw_response_hash is not None


def test_v14_service_records_mechanical_claim_deduplication_warning():
    generated = ScreenPurposeInferenceServiceV14(
        DuplicateClaimClient()
    ).generate(package())

    assert len(generated.inference.supported_capabilities) == 2
    assert generated.warnings == ["deduplicated_functional_claims:1"]


def test_v14_service_records_mechanical_reference_deduplication_warning():
    generated = ScreenPurposeInferenceServiceV14(DuplicateReferenceClient()).generate(package())

    assert generated.inference.supported_capabilities[0].evidence_refs == [
        "column:owner",
        "column:risk",
    ]
    assert generated.warnings == ["deduplicated_evidence_refs:1"]
