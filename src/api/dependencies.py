from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from src.knowledge.answer_builder import AnswerBuilder
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository
from src.knowledge.structural_search_service import StructuralSearchService


async def get_repository(request: Request) -> StructuralKnowledgeRepository:
    return request.app.state.repository


async def get_search_service(request: Request) -> StructuralSearchService:
    return request.app.state.search_service


async def get_answer_builder(request: Request) -> AnswerBuilder:
    return request.app.state.answer_builder


def get_semantic_review_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.semantic_review_session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
