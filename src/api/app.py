from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import chat, health
from src.config.api_settings import ApiSettings
from src.hybrid.factory import HybridRetrieverFactory
from src.knowledge.answer_builder import AnswerBuilder
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository
from src.knowledge.structural_search_service import StructuralSearchService


def create_app(
    settings: ApiSettings | None = None,
    *,
    semantic_review_session_factory: Callable | None = None,
) -> FastAPI:
    settings = settings or ApiSettings()
    repository = StructuralKnowledgeRepository(settings.screen_index_path)
    repository.load()

    app = FastAPI(title="ERP Assistant API", version="0.1.0", docs_url="/api/docs", redoc_url=None)
    app.state.settings = settings
    app.state.repository = repository
    app.state.search_service = StructuralSearchService(
        repository, settings.max_results, settings.minimum_score
    )
    app.state.answer_builder = AnswerBuilder()
    app.state.hybrid_factory = (
        HybridRetrieverFactory() if os.getenv("ERP_ASSISTANT_HYBRID_API") == "1" else None
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.include_router(health.router, prefix="/api", tags=["health"])
    app.include_router(chat.router, prefix="/api", tags=["chat"])
    if settings.semantic_review_api_enabled:
        app.description = (
            "Incluye una API administrativa local provisional de revisión semántica. "
            "Está desactivada por defecto y reviewer_id es declarado por el cliente; "
            "no constituye autenticación ni RBAC. Por defecto sólo acepta clientes "
            "loopback; ERP_ASSISTANT_SEMANTIC_REVIEW_ALLOW_REMOTE=1 permite acceso remoto "
            "únicamente para desarrollo controlado y no agrega autenticación."
        )
        from sqlalchemy.exc import DBAPIError
        from sqlalchemy.orm import sessionmaker

        from src.api.routes.admin_knowledge import router as admin_knowledge_router
        from src.api.routes.semantic_review import (
            AdminSemanticApiError,
            admin_semantic_error_handler,
            admin_validation_error_handler,
        )
        from src.api.routes.semantic_review import (
            router as semantic_review_router,
        )
        from src.config.database_settings import DatabaseSettings
        from src.database.session import create_engine_from_settings

        if semantic_review_session_factory is None:
            engine = create_engine_from_settings(DatabaseSettings())
            semantic_review_session_factory = sessionmaker(
                bind=engine, expire_on_commit=False
            )
            app.state.semantic_review_engine = engine
        app.state.semantic_review_session_factory = semantic_review_session_factory

        @app.middleware("http")
        async def sanitize_admin_failures(request: Request, call_next):
            is_admin = request.url.path.startswith("/api/admin/")
            client_host = request.client.host if request.client is not None else None
            if (
                is_admin
                and not settings.semantic_review_allow_remote
                and client_host not in {"127.0.0.1", "::1"}
            ):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            try:
                return await call_next(request)
            except Exception as exc:
                if not is_admin:
                    raise
                unavailable = isinstance(exc, DBAPIError)
                return JSONResponse(
                    status_code=503 if unavailable else 500,
                    content={
                        "ok": False,
                        "error_class": "StorageUnavailableError"
                        if unavailable
                        else "UnexpectedAdminApiError",
                        "category": "storage_unavailable" if unavailable else "internal_error",
                        "semantic_id": request.path_params.get("semantic_id"),
                        "current_status": None,
                        "detail": "El almacenamiento semántico no está disponible."
                        if unavailable
                        else "Ocurrió un error administrativo inesperado.",
                    },
                )

        app.add_exception_handler(AdminSemanticApiError, admin_semantic_error_handler)
        app.add_exception_handler(RequestValidationError, admin_validation_error_handler)
        app.include_router(
            semantic_review_router,
            prefix="/api/admin",
        )
        app.include_router(admin_knowledge_router, prefix="/api/admin")
    return app


app = create_app()
