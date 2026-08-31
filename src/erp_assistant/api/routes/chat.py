from fastapi import APIRouter, HTTPException, Request

from erp_assistant.api.schemas.chat import ChatRequest, ChatResponse, ChatSource
from erp_assistant.retrieval.conversation_context import ConversationState

router = APIRouter()


@router.post("/chat", response_model=ChatResponse, response_model_by_alias=True)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    conversation_store = request.app.state.conversation_state_store
    conversation_id = conversation_store.resolve_conversation_id(
        payload.conversation_id
    )

    hybrid = request.app.state.hybrid_factory
    try:
        with conversation_store.turn(conversation_id) as turn:
            with hybrid.create(generate=True) as retriever:
                result = retriever.ask(
                    payload.question,
                    generate=True,
                    conversation_state=turn.state,
                )

            if result.get("answer_mode") not in {
                "deterministic_graph",
                "deterministic_semantic",
                "policy_abstention",
                "ollama_grounded",
                "insufficient_evidence",
                "clarification",
            }:
                raise RuntimeError("Hybrid retriever devolvió un answer_mode no soportado")

            next_state = result.get("conversation_state")
            if next_state is not None:
                turn.commit(ConversationState.coerce(next_state))

            return ChatResponse(
                answer=result["answer"],
                conversationId=conversation_id,
                suggestions=[],
                status="answered"
                if result["answer_mode"] != "insufficient_evidence"
                else "not_found",
                sources=[
                    ChatSource(
                        title=s["safe_label"],
                        route=s.get("screen_route") or s.get("route") or "",
                        source_type=s.get("entity_type") or "screen",
                    )
                    for s in result.get("sources", [])[:10]
                ],
                answer_mode=result.get("answer_mode"),
                answerDecision=result.get("answer_decision"),
                intent=result.get("intent"),
                confidence=result.get("confidence"),
                evidence_ids=result.get("evidence_ids", []),
                retrieval=result.get("retrieval"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Servicio de conocimiento temporalmente no disponible",
        ) from exc
