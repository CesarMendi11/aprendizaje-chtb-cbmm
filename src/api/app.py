from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import chat, health
from src.config.api_settings import ApiSettings
from src.hybrid.factory import HybridRetrieverFactory
from src.knowledge.answer_builder import AnswerBuilder
from src.knowledge.structural_knowledge_repository import StructuralKnowledgeRepository
from src.knowledge.structural_search_service import StructuralSearchService


def create_app(settings: ApiSettings | None = None) -> FastAPI:
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
    return app


app = create_app()
