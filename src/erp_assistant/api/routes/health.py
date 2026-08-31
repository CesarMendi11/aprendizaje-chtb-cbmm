from fastapi import APIRouter, Request

from erp_assistant.api.schemas.chat import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/health/dependencies")
async def dependency_health(request: Request):
    settings = request.app.state.settings

    if settings.semantic_review_api_enabled:
        from erp_assistant.api.admin_system_service import collect_admin_system_status

        system_status = collect_admin_system_status(
            request.app.state.semantic_review_session_factory
        )
        dependencies = {
            name: service["status"]
            for name, service in system_status["services"].items()
        }
        return {
            "status": "ok" if system_status["ok"] else "degraded",
            "dependencies": dependencies,
        }

    return {
        "status": "ok",
        "dependencies": {
            "postgresql": "not_probed",
            "neo4j": "not_probed",
            "chroma": "not_probed",
            "semantic_chroma": "not_probed",
            "ollama": "not_probed",
        },
    }
