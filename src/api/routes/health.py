from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import get_repository
from src.api.schemas.chat import HealthResponse
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(
    repository: Annotated[StructuralKnowledgeRepository, Depends(get_repository)],
) -> HealthResponse:
    return HealthResponse(
        knowledge_loaded=repository.knowledge_loaded, screens_count=repository.screens_count
    )
