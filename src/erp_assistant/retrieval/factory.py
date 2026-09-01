from __future__ import annotations

from contextlib import contextmanager

from erp_assistant.config.chroma_settings import ChromaSettings
from erp_assistant.config.neo4j_settings import Neo4jSettings
from erp_assistant.integrations.ollama.embeddings import OllamaEmbeddingClient
from erp_assistant.integrations.ollama.generation import OllamaGenerationClient
from erp_assistant.persistence.postgres.session import database_engine, session_scope
from erp_assistant.projections.chroma.semantic_repository import SemanticChromaRepository
from erp_assistant.projections.chroma.structural_repository import ChromaRepository
from erp_assistant.projections.neo4j.client import Neo4jClient

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
            chroma = ChromaRepository(path=ChromaSettings().path)
            semantic_chroma = SemanticChromaRepository(client=chroma.client)
            yield HybridKnowledgeRetriever(
                session,
                chroma=chroma,
                semantic_chroma=semantic_chroma,
                neo4j=graph,
                embeddings=OllamaEmbeddingClient(),
                generator=OllamaGenerationClient() if generate else None,
                aliases=semantic_aliases_for(erp_id),
            )
