from __future__ import annotations

import os
from contextlib import contextmanager

from scripts.database_common import database_engine, project_path
from src.config.neo4j_settings import Neo4jSettings
from src.database.session import session_scope
from src.graph.client import Neo4jClient
from src.vectorstore import ChromaRepository, OllamaEmbeddingClient
from src.vectorstore.ollama_generation import OllamaGenerationClient

from .aliases import semantic_aliases_for
from .retriever import HybridKnowledgeRetriever


class HybridRetrieverFactory:
    def __init__(self, *, retriever_factory=None):
        self.retriever_factory = retriever_factory

    @contextmanager
    def create(self, *, erp_id=None, generate=True):
        if self.retriever_factory:
            yield self.retriever_factory()
            return
        with session_scope(database_engine()) as session, Neo4jClient(Neo4jSettings()) as graph:
            chroma = ChromaRepository(
                path=os.getenv("ERP_ASSISTANT_CHROMA_PATH")
                or project_path("data/vectorstore/chroma")
            )
            yield HybridKnowledgeRetriever(
                session,
                chroma=chroma,
                neo4j=graph,
                embeddings=OllamaEmbeddingClient(),
                generator=OllamaGenerationClient() if generate else None,
                aliases=semantic_aliases_for(erp_id),
            )
