from __future__ import annotations

import httpx
import pytest

from erp_assistant.integrations.ollama.generation import (
    OllamaGenerationClient,
    OllamaGenerationSettings,
    OllamaWriterSettings,
)
from erp_assistant.semantic.generation.errors import (
    EmptyStructuredOutputError,
    OllamaBodyError,
    OllamaHTTPError,
    OllamaResponseTooLargeError,
    OllamaTimeoutError,
    StructuredModeUnsupportedError,
)
from erp_assistant.semantic.generation.ollama_structured_client import (
    MAX_GENERATED_TEXT_BYTES,
    OllamaStructuredGenerationClient,
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
    assert "think" not in captured


def test_structured_generation_can_disable_thinking_explicitly():
    captured = {}

    def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": "{}"})

    structured, http = client(handler)
    try:
        structured.generate(
            "prompt",
            system="system",
            schema={"type": "object"},
            think=False,
        )
    finally:
        http.close()

    assert captured["think"] is False


def test_structured_generation_rejects_non_boolean_think():
    structured, http = client(lambda request: httpx.Response(200, json={"response": "{}"}))
    try:
        with pytest.raises(ValueError, match="think estructurado inválido"):
            structured.generate(
                "prompt",
                system="system",
                schema={"type": "object"},
                think="false",
            )
    finally:
        http.close()


def test_structured_generation_accepts_explicit_bounded_options():
    captured = {}

    def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": "{}"})

    structured, http = client(handler)
    try:
        structured.generate(
            "prompt",
            system="system",
            schema={"type": "object"},
            options={"temperature": 0, "num_predict": 2048, "num_ctx": 8192},
        )
    finally:
        http.close()

    assert captured["options"] == {
        "temperature": 0,
        "num_predict": 2048,
        "num_ctx": 8192,
    }


@pytest.mark.parametrize(
    "options",
    [
        {"temperature": -1, "num_predict": 1024},
        {"temperature": 0, "num_predict": 0},
        {"temperature": 0, "num_predict": True},
        {"temperature": 0, "num_predict": 1024, "num_ctx": 0},
        {"temperature": 0, "num_predict": 1024, "num_ctx": True},
        {"temperature": 0, "seed": 1},
    ],
)
def test_structured_generation_rejects_invalid_or_unknown_options(options):
    structured, http = client(lambda request: httpx.Response(200, json={"response": "{}"}))
    try:
        with pytest.raises(ValueError):
            structured.generate(
                "prompt",
                system="system",
                schema={"type": "object"},
                options=options,
            )
    finally:
        http.close()


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


def test_writer_settings_are_independent_from_semantic_generation_model(monkeypatch):
    monkeypatch.setenv("ERP_ASSISTANT_GENERATION_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("ERP_ASSISTANT_WRITER_MODEL", "qwen3.5:4b")
    monkeypatch.setenv("ERP_ASSISTANT_WRITER_TIMEOUT", "60")

    assert OllamaGenerationSettings().model == "qwen3.5:9b"

    writer = OllamaWriterSettings()
    assert writer.model == "qwen3.5:4b"
    assert writer.timeout == 60
    assert OllamaGenerationClient().settings.model == "qwen3.5:4b"


def test_structured_timeout_defaults_to_120_and_can_be_overridden():
    settings = OllamaGenerationSettings(timeout=30)
    assert OllamaStructuredGenerationClient(settings).timeout == 120
    assert OllamaStructuredGenerationClient(settings, timeout=45).timeout == 45
    assert OllamaGenerationClient(settings).settings.timeout == 30


def test_generation_settings_read_environment_at_instantiation(monkeypatch):
    monkeypatch.setenv("ERP_ASSISTANT_OLLAMA_URL", "http://ollama.env:11434/")
    monkeypatch.setenv("ERP_ASSISTANT_GENERATION_MODEL", "env-model")
    monkeypatch.setenv("ERP_ASSISTANT_OLLAMA_TIMEOUT", "12")
    monkeypatch.setenv("ERP_ASSISTANT_OLLAMA_STRUCTURED_TIMEOUT", "34")

    settings = OllamaGenerationSettings()

    assert settings.url == "http://ollama.env:11434"
    assert settings.model == "env-model"
    assert settings.timeout == 12
    assert settings.structured_timeout == 34


def test_text_generation_client_uses_settings_and_returns_trimmed_text():
    captured = {}

    def handler(request):
        captured.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"response": "  respuesta grounded  "})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="http://ollama.test")
    settings = OllamaGenerationSettings(
        url="http://ollama.test",
        model="text-model",
        timeout=7,
    )
    try:
        result = OllamaGenerationClient(settings, client=http).generate(
            "pregunta",
            system="system",
        )
    finally:
        http.close()

    assert result == "respuesta grounded"
    assert captured == {
        "model": "text-model",
        "prompt": "pregunta",
        "system": "system",
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 512},
    }
