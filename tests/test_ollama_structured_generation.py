from __future__ import annotations

import httpx
import pytest

from src.analysis.generation.errors import (
    EmptyStructuredOutputError,
    OllamaBodyError,
    OllamaHTTPError,
    OllamaResponseTooLargeError,
    OllamaTimeoutError,
    StructuredModeUnsupportedError,
)
from src.analysis.generation.ollama_structured_client import (
    MAX_GENERATED_TEXT_BYTES,
    OllamaStructuredGenerationClient,
)
from src.vectorstore.ollama_generation import (
    OllamaGenerationClient,
    OllamaGenerationSettings,
)


def client(handler, *, mode="json_schema"):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://ollama.test")
    settings = OllamaGenerationSettings(url="http://ollama.test", model="test-model", timeout=1)
    return OllamaStructuredGenerationClient(settings, client=http, mode=mode), http


def test_json_schema_mode_sends_schema_and_canonical_options():
    captured = {}

    def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": "{}"})

    structured, http = client(handler)
    try:
        result = structured.generate("prompt", system="system", schema={"type": "object"})
    finally:
        http.close()
    assert result.mode == "json_schema"
    assert captured["format"] == {"type": "object"}
    assert captured["stream"] is False
    assert captured["options"] == {"temperature": 0, "num_predict": 1024}


def test_json_compatibility_mode_is_explicit():
    captured = {}

    def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": "{}"})

    structured, http = client(handler, mode="json")
    try:
        result = structured.generate("prompt", system="system", schema={"type": "object"})
    finally:
        http.close()
    assert result.mode == "json" and captured["format"] == "json"


@pytest.mark.parametrize(
    "status,error", [(400, StructuredModeUnsupportedError), (500, OllamaHTTPError)]
)
def test_http_errors_are_typed_and_do_not_leak_body(status, error):
    structured, http = client(lambda request: httpx.Response(status, text="secret raw body"))
    try:
        with pytest.raises(error) as captured:
            structured.generate("prompt", system="system", schema={})
    finally:
        http.close()
    assert "secret raw body" not in str(captured.value)


def test_timeout_invalid_body_empty_and_oversized_output():
    cases = [
        (
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
            OllamaTimeoutError,
        ),
        (
            lambda request: httpx.Response(
                200, text="not-json", headers={"content-type": "application/json"}
            ),
            OllamaBodyError,
        ),
        (lambda request: httpx.Response(200, json={"response": " "}), EmptyStructuredOutputError),
        (
            lambda request: httpx.Response(
                200, json={"response": "x" * (MAX_GENERATED_TEXT_BYTES + 1)}
            ),
            OllamaResponseTooLargeError,
        ),
    ]
    for handler, error in cases:
        structured, http = client(handler)
        try:
            with pytest.raises(error):
                structured.generate("prompt", system="system", schema={})
        finally:
            http.close()


def test_existing_text_generation_client_contract_is_unchanged():
    assert OllamaGenerationClient.generate.__annotations__["return"] == "str"
    settings = OllamaGenerationSettings(model="existing-model")
    assert OllamaGenerationClient(settings).settings.model == "existing-model"


def test_structured_timeout_defaults_to_120_and_can_be_overridden():
    settings = OllamaGenerationSettings(timeout=30)
    assert OllamaStructuredGenerationClient(settings).timeout == 120
    assert OllamaStructuredGenerationClient(settings, timeout=45).timeout == 45
    assert OllamaGenerationClient(settings).settings.timeout == 30
