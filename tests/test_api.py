from __future__ import annotations

import asyncio
import json
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
