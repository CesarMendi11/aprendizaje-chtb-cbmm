from fastapi import APIRouter, HTTPException, Request

from erp_assistant.api.schemas.chat import ChatRequest, ChatResponse, ChatSource
from erp_assistant.retrieval.conversation_context import ConversationState

router = APIRouter()


@router.post("/chat", response_model=ChatResponse, response_model_by_alias=True)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    conversation_store = request.app.state.conversation_state_store
    conversation_id = conversation_store.resolve_conversation_id(payload.conversation_id)

    hybrid = request.app.state.hybrid_factory
    condition = payload.experiment_condition
    semantic_enabled = condition in {"B", "C"}
    writer_enabled = condition == "C"
    try:
        with conversation_store.turn(conversation_id) as turn:
            with hybrid.create(generate=writer_enabled) as retriever:
                ask_kwargs = {
                    "generate": writer_enabled,
                    "conversation_state": turn.state,
                }
                if not writer_enabled:
                    ask_kwargs["writer_enabled"] = False
                if not semantic_enabled:
                    ask_kwargs["semantic_enabled"] = False
                if not payload.graph_enabled:
                    ask_kwargs["graph_enabled"] = False
                if payload.context is not None and payload.context.current_route is not None:
                    ask_kwargs["current_route"] = payload.context.current_route

                result = retriever.ask(
                    payload.question,
                    **ask_kwargs,
                )

            if result.get("answer_mode") not in {
                "deterministic_graph",
                "deterministic_semantic",
                "policy_abstention",
                "ollama_grounded",
                "insufficient_evidence",
                "clarification",
                "deterministic_evidence",
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
                experimentCondition=condition,
                graphEnabled=payload.graph_enabled,
                graphExpansion=result.get("graph_expansion"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Servicio de conocimiento temporalmente no disponible",
        ) from exc
