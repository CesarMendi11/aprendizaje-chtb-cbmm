from typing import Annotated

from fastapi import APIRouter, Depends, Request

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


@router.get("/health/dependencies")
async def dependency_health(
    request: Request,
    repository: Annotated[StructuralKnowledgeRepository, Depends(get_repository)],
):
    legacy_status = "ready" if repository.knowledge_loaded else "unavailable"
    settings = request.app.state.settings

    if settings.semantic_review_api_enabled:
        # Reuse the governed runtime probes that already back
        # /api/admin/system/status.  The optional legacy screen index must not
        # decide whether PostgreSQL/Neo4j/Chroma/Ollama are healthy.
        from src.api.admin_system_service import collect_admin_system_status

        system_status = collect_admin_system_status(
            request.app.state.semantic_review_session_factory
        )
        dependencies = {
            name: service["status"]
            for name, service in system_status["services"].items()
        }
        dependencies["legacy_structural"] = legacy_status
        return {
            "status": "ok" if system_status["ok"] else "degraded",
            "dependencies": dependencies,
        }

    # Without the governed/admin runtime enabled, keep this endpoint as a
    # lightweight capability report.  Do not pretend that unprobed services
    # are healthy or unhealthy.
    return {
        "status": "ok",
        "dependencies": {
            "postgresql": "not_probed",
            "neo4j": "not_probed",
            "chroma": "not_probed",
            "semantic_chroma": "not_probed",
            "ollama": "not_probed",
            "legacy_structural": legacy_status,
        },
    }
