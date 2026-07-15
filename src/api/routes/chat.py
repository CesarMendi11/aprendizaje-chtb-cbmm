from typing import Annotated

from fastapi import APIRouter, Depends

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
) -> ChatResponse:
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
