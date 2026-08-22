from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

import httpx
import pytest

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.knowledge.text_normalizer import normalize_text


@pytest.fixture
def screen_index(tmp_path):
    screens = [
        {
            "route": "/admin/home",
            "title": "Dashboard",
            "main_visible_text": "Página de inicio",
        },
        {
            "route": "/admin/cuentasxcobrar/retenciones",
            "title": "Retenciones",
            "main_visible_text": "Filtros de consulta y tabla de resultados",
            "inputs": [
                {"label": "RUC", "placeholder": "0000000000001"},
                {"label": "Fecha desde"},
                {"label": "Estado"},
            ],
            "buttons": [{"text": "Buscar"}],
            "tables": [{"headers": ["ESTADO", "RUC", "TOTAL RETENIDO"]}],
            "local_links": [{"text": "Dashboard", "href": "/"}],
        },
        {
            "route": "/admin/cuentasxcobrar/lista-facturas",
            "title": "Lista de facturas",
            "main_visible_text": "Consulta de facturas emitidas",
            "inputs": [{"label": "Núm. comprobante"}],
            "tables": [{"headers": ["FECHA EMISIÓN", "TOTAL"]}],
        },
        {
            "route": "/admin/general/personas",
            "title": "Personas",
            "buttons": [{"text": "Buscar"}, {"aria_label": "Filtrar"}],
            "tables": [{"headers": ["CÉDULA", "NOMBRE"]}],
        },
    ]
    path = tmp_path / "screen_index.json"
    path.write_text(
        json.dumps({"index_type": "erp_screen_index", "screens": screens}), encoding="utf-8"
    )
    return path


@pytest.fixture
def settings(screen_index):
    return replace(ApiSettings(), screen_index_path=screen_index, minimum_score=2.0)


@pytest.fixture
def client(settings):
    return ApiClient(create_app(settings))


class ApiClient:
    def __init__(self, app):
        self.app = app

    def request(self, method, url, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(send())

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def options(self, url, **kwargs):
        return self.request("OPTIONS", url, **kwargs)


def ask(client, question, *, route=None, conversation_id="conversation-1"):
    context = {"currentRoute": route} if route else {}
    return client.post(
        "/api/chat",
        json={"question": question, "conversationId": conversation_id, "context": context},
    )


def test_health_with_loaded_knowledge(client):
    assert client.get("/api/health").json() == {
        "status": "ok",
        "service": "erp-assistant-api",
        "knowledge_loaded": True,
        "screens_count": 4,
    }


def test_health_without_knowledge_file(settings, tmp_path):
    app = create_app(replace(settings, screen_index_path=tmp_path / "missing.json"))
    assert ApiClient(app).get("/api/health").json()["knowledge_loaded"] is False


def test_missing_legacy_index_is_optional_and_quiet(settings, tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    missing = tmp_path / "missing.json"

    app = create_app(replace(settings, screen_index_path=missing))
    payload = ApiClient(app).get("/api/health/dependencies").json()

    assert payload == {
        "status": "ok",
        "dependencies": {
            "postgresql": "not_probed",
            "neo4j": "not_probed",
            "chroma": "not_probed",
            "semantic_chroma": "not_probed",
            "ollama": "not_probed",
            "legacy_structural": "unavailable",
        },
    }
    assert "No se pudo cargar el conocimiento estructural" not in caplog.text


def test_governed_dependency_health_does_not_use_legacy_index_as_authority(
    settings, tmp_path, monkeypatch
):
    from src.api import admin_system_service

    monkeypatch.setattr(
        admin_system_service,
        "collect_admin_system_status",
        lambda _factory: {
            "ok": True,
            "services": {
                "postgresql": {"status": "online"},
                "neo4j": {"status": "online"},
                "chroma": {"status": "ready"},
                "semantic_chroma": {"status": "ready"},
                "ollama": {"status": "online"},
            },
            "knowledge": {},
        },
    )

    app = create_app(
        replace(
            settings,
            screen_index_path=tmp_path / "missing.json",
            semantic_review_api_enabled=True,
        ),
        semantic_review_session_factory=object(),
        pipeline_job_dispatcher=object(),
    )
    payload = ApiClient(app).get("/api/health/dependencies").json()

    assert payload["status"] == "ok"
    assert payload["dependencies"] == {
        "postgresql": "online",
        "neo4j": "online",
        "chroma": "ready",
        "semantic_chroma": "ready",
        "ollama": "online",
        "legacy_structural": "unavailable",
    }


def test_blank_question_is_rejected(client):
    response = ask(client, "   ")
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("question", "expected_title"),
    [
        ("¿Dónde consulto RETENCIONES?", "Retenciones"),
        ("¿Cómo reviso las facturas?", "Lista de facturas"),
    ],
)
def test_locates_screen(client, question, expected_title):
    payload = ask(client, question).json()
    assert payload["status"] == "answered"
    assert payload["sources"][0]["title"] == expected_title


def test_describes_current_screen(client):
    payload = ask(
        client, "¿Qué puedo hacer en esta pantalla?", route="/admin/cuentasxcobrar/retenciones"
    ).json()
    assert payload["status"] == "answered"
    assert "Retenciones" in payload["answer"]
    assert "3 campos" in payload["answer"]


def test_lists_only_observed_fields(client):
    answer = ask(
        client, "¿Qué campos tiene esta pantalla?", route="/admin/cuentasxcobrar/retenciones"
    ).json()["answer"]
    assert all(field in answer for field in ("RUC", "Fecha desde", "Estado"))
    assert "Fecha hasta" not in answer


def test_unknown_query(client):
    payload = ask(client, "Explícame astrofísica cuántica avanzada").json()
    assert payload["status"] == "not_found"
    assert payload["sources"] == []


def test_corrupt_json_starts_server(settings, tmp_path):
    corrupt = tmp_path / "screen_index.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    client = ApiClient(create_app(replace(settings, screen_index_path=corrupt)))
    assert client.get("/api/health").json()["knowledge_loaded"] is False
    assert ask(client, "¿Dónde están las retenciones?").json()["status"] == "error"


def test_local_cors(client):
    response = client.options(
        "/api/chat",
        headers={"Origin": "http://localhost:4200", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"
    denied = client.options(
        "/api/chat",
        headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in denied.headers


def test_conversation_id_and_source_contract(client):
    payload = ask(client, "¿Dónde consulto retenciones?", conversation_id="abc-123").json()
    assert payload["conversationId"] == "abc-123"
    assert payload["sources"] == [
        {
            "title": "Retenciones",
            "route": "/admin/cuentasxcobrar/retenciones",
            "sourceType": "screen",
        }
    ]


def test_accent_and_case_normalization():
    assert normalize_text("  RETENCIÓN,   MÓDULO  ") == "retencion modulo"


def test_chat_does_not_execute_mutative_actions(client, monkeypatch):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("No debe ejecutarse ninguna acción")

    monkeypatch.setattr("subprocess.run", forbidden)
    payload = ask(client, "Guarda y elimina todos los registros").json()
    assert payload["status"] == "not_found"
    assert called is False


def test_hybrid_chat_does_not_require_legacy_screen_index(settings, tmp_path):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True):
            return {
                "answer": "Respuesta híbrida autorizada.",
                "answer_mode": "deterministic_graph",
                "intent": "SCREEN_LOCATION",
                "confidence": "high",
                "evidence_ids": ["screen:test"],
                "retrieval": {"validated_items": 1},
                "sources": [
                    {
                        "safe_label": "Pantalla de prueba",
                        "screen_route": "/admin/test",
                    }
                ],
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    app = create_app(
        replace(settings, screen_index_path=tmp_path / "missing.json")
    )
    app.state.hybrid_factory = Factory()

    payload = ask(ApiClient(app), "¿Dónde está la pantalla de prueba?").json()

    assert payload["status"] == "answered"
    assert payload["answer"] == "Respuesta híbrida autorizada."
    assert payload["sources"][0]["title"] == "Pantalla de prueba"


def test_chat_returns_deterministic_semantic_answer(settings):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True):
            return {
                "answer": "Permite buscar y consultar retenciones.",
                "answer_mode": "deterministic_semantic",
                "intent": "SCREEN_PURPOSE",
                "confidence": "high",
                "evidence_ids": ["semantic:retenciones-purpose", "screen:retenciones"],
                "retrieval": {
                    "semantic_hits": 4,
                    "semantic_candidates": 1,
                    "approved_semantic_hits": 1,
                    "graph_neighbors": 3,
                    "validated_items": 4,
                },
                "sources": [
                    {
                        "safe_label": "Retenciones",
                        "screen_route": "/admin/cuentasxcobrar/retenciones",
                    }
                ],
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    app = create_app(settings)
    app.state.hybrid_factory = Factory()
    payload = ask(ApiClient(app), "¿Para qué sirve la pantalla Retenciones?").json()

    assert payload["status"] == "answered"
    assert payload["answer"] == "Permite buscar y consultar retenciones."
    assert payload["answer_mode"] == "deterministic_semantic"
    assert payload["intent"] == "SCREEN_PURPOSE"
    assert payload["sources"] == [
        {
            "title": "Retenciones",
            "route": "/admin/cuentasxcobrar/retenciones",
            "sourceType": "screen",
        }
    ]
    assert payload["retrieval"]["approved_semantic_hits"] == 1


def test_chat_returns_ollama_grounded_answer_as_answered(settings):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True):
            return {
                "answer": "En Retenciones puedes consultar información usando los criterios disponibles.",
                "answer_mode": "ollama_grounded",
                "intent": None,
                "confidence": None,
                "evidence_ids": ["screen:retenciones", "field:ruc", "control:buscar"],
                "retrieval": {
                    "semantic_hits": 3,
                    "semantic_candidates": 1,
                    "approved_semantic_hits": 1,
                    "graph_neighbors": 4,
                    "validated_items": 5,
                },
                "sources": [
                    {
                        "safe_label": "Retenciones",
                        "screen_route": "/admin/cuentasxcobrar/retenciones",
                    }
                ],
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    app = create_app(settings)
    app.state.hybrid_factory = Factory()
    payload = ask(
        ApiClient(app),
        "Explícame qué información está disponible en Retenciones y cómo se relaciona.",
    ).json()

    assert payload["status"] == "answered"
    assert payload["answer_mode"] == "ollama_grounded"
    assert "Retenciones" in payload["answer"]
    assert payload["sources"][0]["title"] == "Retenciones"


def test_chat_returns_hybrid_clarification_without_legacy_fallback(settings):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True):
            return {
                "answer": (
                    'Encontré varias coincidencias para "RUC". '
                    "Indícame la pantalla o el módulo al que te refieres para poder elegir la correcta."
                ),
                "answer_mode": "clarification",
                "answer_decision": {
                    "decision": "CLARIFICATION",
                    "reason": "entity_resolution_ambiguous",
                    "intent": "LOCATE_FIELD",
                    "confidence": "high",
                },
                "intent": "LOCATE_FIELD",
                "confidence": "high",
                "evidence_ids": [],
                "retrieval": {"selected_sources": 0},
                "sources": [],
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    app = create_app(settings)
    app.state.hybrid_factory = Factory()

    payload = ask(
        ApiClient(app),
        "¿Dónde aparece la identificación tributaria?",
    ).json()

    assert payload["status"] == "answered"
    assert payload["answer_mode"] == "clarification"
    assert payload["answerDecision"]["decision"] == "CLARIFICATION"
    assert payload["answerDecision"]["reason"] == "entity_resolution_ambiguous"
    assert payload["sources"] == []
    assert "RUC" in payload["answer"]
