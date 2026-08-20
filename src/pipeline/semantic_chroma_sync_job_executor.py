from __future__ import annotations

import os
import uuid
from pathlib import Path

from src.database.enums import KnowledgeVersionStatus, PipelineJobScope
from src.database.models import KnowledgeVersionRecord
from src.database.services.semantic_chroma_sync_service import SemanticChromaSyncService
from src.vectorstore import OllamaEmbeddingClient, SemanticChromaRepository


class SemanticChromaSyncJobExecutionError(RuntimeError):
    pass


class SemanticChromaSyncJobExecutor:
    """Project fresh, approved semantic proposals from the captured ACTIVE version."""

    def __init__(
        self,
        session_factory,
        *,
        repository_factory=None,
        embeddings_factory=None,
        service_factory=None,
    ):
        self.session_factory = session_factory
        self.repository_factory = repository_factory or self._default_repository
        self.embeddings_factory = embeddings_factory or OllamaEmbeddingClient
        self.service_factory = service_factory or (lambda session, **kwargs: SemanticChromaSyncService(session, **kwargs))

    @staticmethod
    def _default_repository():
        path = os.getenv("ERP_ASSISTANT_CHROMA_PATH") or Path("data/vectorstore/chroma")
        return SemanticChromaRepository(path=path)

    def execute(self, *, job_id, scope, target, parameters, progress):
        if scope != PipelineJobScope.VERSION:
            raise SemanticChromaSyncJobExecutionError("Semantic Chroma sync requiere scope=version")
        version_id = self._version_id(parameters)

        progress(
            "validating_active_version",
            {"work_units": 1, "progress_total": 4, "knowledge_version_id": str(version_id)},
        )
        with self.session_factory() as session:
            version = self._require_active_version(session, version_id, parameters)
            _version, documents, summary = self.service_factory(session).prepare(
                erp_id=version.erp_id,
                knowledge_version=version.knowledge_version,
            )

        progress(
            "semantic_documents_prepared",
            {
                "work_units": 2,
                "progress_total": 4,
                "publishable_proposals": summary.get("publishable_proposals", 0),
                "documents": len(documents),
                "skipped": summary.get("skipped", 0),
            },
        )

        try:
            repository = self.repository_factory()
            embeddings = self.embeddings_factory()
            progress(
                "embedding_and_syncing_semantics",
                {
                    "work_units": 3,
                    "progress_total": 4,
                    "documents": len(documents),
                    "embedding_model": getattr(embeddings, "model", None),
                },
            )
            with self.session_factory() as session:
                version = self._require_active_version(session, version_id, parameters)
                result = self.service_factory(
                    session,
                    repository=repository,
                    embeddings=embeddings,
                ).run(
                    erp_id=version.erp_id,
                    knowledge_version=version.knowledge_version,
                )
                summary = dict(result.summary)
        except SemanticChromaSyncJobExecutionError:
            raise
        except Exception as exc:
            raise SemanticChromaSyncJobExecutionError(str(exc)[:400]) from exc

        progress(
            "semantic_chroma_synced",
            {
                "work_units": 4,
                "progress_total": 4,
                "documents": summary.get("documents", 0),
                "inserted_or_updated": summary.get("inserted_or_updated", 0),
                "removed_stale": summary.get("removed_stale", 0),
                "embedding_dimensions": summary.get("embedding_dimensions"),
            },
        )
        return {
            "target": "semantic_chromadb",
            "active_only": True,
            "erp_id": parameters["erp_id"],
            "knowledge_version": parameters["knowledge_version"],
            "knowledge_version_id": parameters["knowledge_version_id"],
            **summary,
        }

    @staticmethod
    def _version_id(parameters):
        try:
            return uuid.UUID(str(parameters["knowledge_version_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChromaSyncJobExecutionError("knowledge_version_id inválido") from exc

    @staticmethod
    def _require_active_version(session, version_id, parameters):
        version = session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise SemanticChromaSyncJobExecutionError("Versión de conocimiento no encontrada")
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise SemanticChromaSyncJobExecutionError(
                "La versión capturada dejó de ser ACTIVE antes de sincronizar"
            )
        if (
            version.knowledge_version != parameters.get("knowledge_version")
            or version.erp_id != parameters.get("erp_id")
        ):
            raise SemanticChromaSyncJobExecutionError("La identidad de la versión activa cambió")
        return version
