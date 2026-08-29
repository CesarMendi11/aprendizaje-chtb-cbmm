from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from src.api.app import create_app
from src.config.api_settings import ApiSettings
from src.knowledge.text_normalizer import normalize_text


@pytest.fixture
def settings():
    return ApiSettings()


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


def test_health_is_runtime_liveness(client):
    assert client.get("/api/health").json() == {
        "status": "ok",
        "service": "erp-assistant-api",
    }


def test_dependency_health_without_admin_reports_unprobed(settings):
    app = create_app(replace(settings, semantic_review_api_enabled=False))
    payload = ApiClient(app).get("/api/health/dependencies").json()

    assert payload == {
        "status": "ok",
        "dependencies": {
            "postgresql": "not_probed",
            "neo4j": "not_probed",
            "chroma": "not_probed",
            "semantic_chroma": "not_probed",
            "ollama": "not_probed",
        },
    }


def test_governed_dependency_health_reports_runtime_services(settings, monkeypatch):
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
        replace(settings, semantic_review_api_enabled=True),
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
    }


def test_blank_question_is_rejected(client):
    response = ask(client, "   ")
    assert response.status_code == 422


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


def test_accent_and_case_normalization():
    assert normalize_text("  RETENCIÓN,   MÓDULO  ") == "retencion modulo"


def test_hybrid_chat_uses_hybrid_runtime(settings):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True, conversation_state=None):
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

    app = create_app(settings)
    app.state.hybrid_factory = Factory()

    payload = ask(ApiClient(app), "¿Dónde está la pantalla de prueba?").json()

    assert payload["status"] == "answered"
    assert payload["answer"] == "Respuesta híbrida autorizada."
    assert payload["sources"][0]["title"] == "Pantalla de prueba"


def test_chat_returns_deterministic_semantic_answer(settings):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True, conversation_state=None):
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
        def ask(self, question, *, generate=True, conversation_state=None):
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
        def ask(self, question, *, generate=True, conversation_state=None):
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



def test_hybrid_api_persists_governed_state_by_conversation_id(settings):
    from contextlib import contextmanager

    from src.hybrid.conversation_context import ConversationEntity, ConversationState
    from src.hybrid.conversation_store import ConversationStateStore

    calls = []

    class Retriever:
        def ask(self, question, *, generate=True, conversation_state=None):
            state = ConversationState.coerce(conversation_state)
            calls.append((question, state))
            screen = state.current_screen or ConversationEntity(
                canonical_id="screen:ano",
                entity_type="screen",
                safe_label="Año",
                route="/admin/general/anios",
            )
            next_state = ConversationState(
                erp_id="erp:test",
                knowledge_version="kv:test",
                current_screen=screen,
                turn_index=state.turn_index + 1,
            )
            return {
                "answer": "Respuesta gobernada.",
                "answer_mode": "deterministic_graph",
                "answer_decision": {
                    "decision": "DETERMINISTIC_ANSWER",
                    "reason": "test",
                    "intent": "SCREEN_PURPOSE",
                    "confidence": "high",
                },
                "intent": "SCREEN_PURPOSE",
                "confidence": "high",
                "evidence_ids": [screen.canonical_id],
                "retrieval": {"validated_items": 1},
                "sources": [
                    {
                        "safe_label": screen.safe_label,
                        "screen_route": screen.route,
                    }
                ],
                "conversation_state": next_state.as_dict(),
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    store = ConversationStateStore()
    app = create_app(settings, conversation_state_store=store)
    app.state.hybrid_factory = Factory()
    client = ApiClient(app)

    first = ask(client, "¿Dónde está Año?", conversation_id="conv-a")
    assert first.status_code == 200
    assert calls[-1][1].turn_index == 0

    second = ask(client, "¿Y para qué sirve?", conversation_id="conv-a")
    assert second.status_code == 200
    assert calls[-1][1].turn_index == 1
    assert calls[-1][1].current_screen.safe_label == "Año"
    assert store.load("conv-a").turn_index == 2

    ask(client, "¿Y para qué sirve?", conversation_id="conv-b")
    assert calls[-1][1].turn_index == 0
    assert calls[-1][1].current_screen is None


def test_hybrid_api_generates_conversation_id_when_missing(settings):
    from contextlib import contextmanager

    from src.hybrid.conversation_context import ConversationState
    from src.hybrid.conversation_store import ConversationStateStore

    class Retriever:
        def ask(self, question, *, generate=True, conversation_state=None):
            state = ConversationState.coerce(conversation_state)
            return {
                "answer": "OK",
                "answer_mode": "deterministic_graph",
                "intent": "LOCATE_SCREEN",
                "confidence": "high",
                "evidence_ids": [],
                "retrieval": {},
                "sources": [],
                "conversation_state": ConversationState(
                    erp_id="erp:test",
                    knowledge_version="kv:test",
                    turn_index=state.turn_index + 1,
                ).as_dict(),
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    store = ConversationStateStore(id_factory=lambda: "server-generated-id")
    app = create_app(settings, conversation_state_store=store)
    app.state.hybrid_factory = Factory()
    client = ApiClient(app)

    response = client.post("/api/chat", json={"question": "Hola"})

    assert response.status_code == 200
    assert response.json()["conversationId"] == "server-generated-id"
    assert store.load("server-generated-id").turn_index == 1


def test_client_cannot_submit_conversation_state(settings):
    app = create_app(settings)
    response = ApiClient(app).post(
        "/api/chat",
        json={
            "question": "¿Y para qué sirve?",
            "conversationId": "conv-a",
            "conversationState": {
                "current_screen": {
                    "canonical_id": "screen:forged",
                    "entity_type": "screen",
                    "safe_label": "Forjada",
                }
            },
        },
    )

    assert response.status_code == 422


def test_blank_conversation_id_is_replaced_server_side(settings):
    from contextlib import contextmanager

    from src.hybrid.conversation_store import ConversationStateStore

    class Retriever:
        def ask(self, question, *, generate=True, conversation_state=None):
            return {
                "answer": "OK",
                "answer_mode": "deterministic_graph",
                "sources": [],
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    store = ConversationStateStore(id_factory=lambda: "replacement-id")
    app = create_app(settings, conversation_state_store=store)
    app.state.hybrid_factory = Factory()
    response = ApiClient(app).post(
        "/api/chat",
        json={
            "question": "¿Dónde consulto retenciones?",
            "conversationId": "   ",
        },
    )

    assert response.status_code == 200
    assert response.json()["conversationId"] == "replacement-id"


def test_hybrid_chat_preserves_canonical_source_types(settings):
    from contextlib import contextmanager

    class Retriever:
        def ask(self, question, *, generate=True, conversation_state=None):
            return {
                "answer": (
                    'La pantalla "Dashboard" está disponible directamente en el ERP '
                    '"ERP Cuerpo de Bomberos Municipal de Machala".'
                ),
                "answer_mode": "deterministic_graph",
                "answer_decision": {
                    "decision": "DETERMINISTIC_ANSWER",
                    "reason": "deterministic_structural_answer",
                    "intent": "LOCATE_SCREEN",
                    "confidence": "high",
                },
                "intent": "LOCATE_SCREEN",
                "confidence": "high",
                "evidence_ids": ["erp:cbmm", "screen:dashboard"],
                "retrieval": {"selected_sources": 2},
                "sources": [
                    {
                        "canonical_id": "screen:dashboard",
                        "entity_type": "screen",
                        "safe_label": "Dashboard",
                        "screen_route": "/admin/home",
                    },
                    {
                        "canonical_id": "erp:cbmm",
                        "entity_type": "erp_system",
                        "safe_label": "ERP Cuerpo de Bomberos Municipal de Machala",
                        "screen_route": None,
                    },
                ],
            }

    class Factory:
        @contextmanager
        def create(self, *, generate=True):
            yield Retriever()

    app = create_app(settings)
    app.state.hybrid_factory = Factory()
    payload = ask(ApiClient(app), "¿Dónde está Dashboard?").json()

    assert payload["status"] == "answered"
    assert payload["sources"] == [
        {
            "title": "Dashboard",
            "route": "/admin/home",
            "sourceType": "screen",
        },
        {
            "title": "ERP Cuerpo de Bomberos Municipal de Machala",
            "route": "",
            "sourceType": "erp_system",
        },
    ]
