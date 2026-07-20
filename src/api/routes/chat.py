from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.dependencies import get_answer_builder, get_repository, get_search_service
from src.api.schemas.chat import ChatRequest, ChatResponse, ChatSource
from src.knowledge.answer_builder import NOT_FOUND, AnswerBuilder
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository
from src.knowledge.structural_search_service import StructuralSearchService

router = APIRouter()


@router.post("/chat", response_model=ChatResponse, response_model_by_alias=True)
async def chat(
    payload: ChatRequest,
    repository: Annotated[StructuralKnowledgeRepository, Depends(get_repository)],
    search_service: Annotated[StructuralSearchService, Depends(get_search_service)],
    answer_builder: Annotated[AnswerBuilder, Depends(get_answer_builder)],
    request: Request,
) -> ChatResponse:
    hybrid = getattr(request.app.state, "hybrid_factory", None)
    if hybrid is not None:
        try:
            with hybrid.create(generate=True) as retriever:
                result = retriever.ask(payload.question, generate=True)
            if result.get("answer_mode") in {
                "deterministic_graph",
                "policy_abstention",
                "ollama_grounded",
                "insufficient_evidence",
            }:
                return ChatResponse(
                    answer=result["answer"],
                    conversationId=payload.conversation_id,
                    suggestions=[],
                    status="answered"
                    if result["answer_mode"] != "insufficient_evidence"
                    else "not_found",
                    sources=[
                        ChatSource(title=s["safe_label"], route=s.get("screen_route") or "")
                        for s in result.get("sources", [])[:10]
                    ],
                    answer_mode=result.get("answer_mode"),
                    intent=result.get("intent"),
                    confidence=result.get("confidence"),
                    evidence_ids=result.get("evidence_ids", []),
                    retrieval=result.get("retrieval"),
                )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Servicio de conocimiento temporalmente no disponible"
            ) from exc
    if not repository.knowledge_loaded:
        return ChatResponse(
            answer=NOT_FOUND,
            conversationId=payload.conversation_id,
            suggestions=[],
            status="error",
            sources=[],
        )
    current_route = payload.context.current_route if payload.context else None
    result = search_service.search(payload.question, current_route)
    answer, status, suggestions = answer_builder.build(payload.question, result)
    sources = []
    if status == "answered" and result.best:
        sources.append(
            ChatSource(title=result.best.screen.display_title, route=result.best.screen.route)
        )
    return ChatResponse(
        answer=answer,
        conversationId=payload.conversation_id,
        suggestions=suggestions,
        status=status,
        sources=sources,
    )
