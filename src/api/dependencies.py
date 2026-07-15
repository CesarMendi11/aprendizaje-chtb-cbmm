from fastapi import Request

from src.knowledge.answer_builder import AnswerBuilder
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository
from src.knowledge.structural_search_service import StructuralSearchService


async def get_repository(request: Request) -> StructuralKnowledgeRepository:
    return request.app.state.repository


async def get_search_service(request: Request) -> StructuralSearchService:
    return request.app.state.search_service


async def get_answer_builder(request: Request) -> AnswerBuilder:
    return request.app.state.answer_builder
