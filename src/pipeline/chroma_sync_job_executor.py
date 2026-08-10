from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from src.database.enums import KnowledgeVersionStatus, PipelineJobScope
from src.database.models import KnowledgeVersionRecord
from src.database.services import ChromaSyncService
from src.vectorstore import ChromaRepository, OllamaEmbeddingClient


class ChromaSyncJobExecutionError(RuntimeError):
    pass


class ChromaSyncJobExecutor:
    """Index approved/corrected knowledge from one explicitly captured ACTIVE version."""

    def __init__(
        self,
        session_factory,
        *,
        repository_factory=None,
        embeddings_factory=None,
    ):
        self.session_factory = session_factory
        self.repository_factory = repository_factory or self._default_repository
        self.embeddings_factory = embeddings_factory or OllamaEmbeddingClient

    @staticmethod
    def _default_repository():
        path = os.getenv("ERP_ASSISTANT_CHROMA_PATH") or Path("data/vectorstore/chroma")
        return ChromaRepository(path=path)

    def execute(self, *, job_id, scope, target, parameters, progress):
        if scope != PipelineJobScope.VERSION:
            raise ChromaSyncJobExecutionError("Chroma sync requiere scope=version")
        version_id = self._version_id(parameters)

        progress(
            "validating_active_version",
            {"work_units": 1, "progress_total": 4, "knowledge_version_id": str(version_id)},
        )
        with self.session_factory() as session:
            version = self._require_active_version(session, version_id, parameters)
            _version, documents, summary = ChromaSyncService(session).prepare(
                erp_id=version.erp_id,
                knowledge_version=version.knowledge_version,
            )

        progress(
            "documents_prepared",
            {
                "work_units": 2,
                "progress_total": 4,
                "eligible_items": summary.get("eligible_items", 0),
                "documents": len(documents),
                "skipped": summary.get("skipped", 0),
            },
        )

        try:
            repository = self.repository_factory()
            embeddings = self.embeddings_factory()
            progress(
                "embedding_and_syncing",
                {
                    "work_units": 3,
                    "progress_total": 4,
                    "documents": len(documents),
                    "embedding_model": getattr(embeddings, "model", None),
                },
            )
            with self.session_factory.begin() as session:
                version = self._require_active_version(session, version_id, parameters)
                result = ChromaSyncService(
                    session,
                    repository=repository,
                    embeddings=embeddings,
                ).run(
                    erp_id=version.erp_id,
                    knowledge_version=version.knowledge_version,
                )
                result_summary = dict(result.summary)
            if result.status != "succeeded":
                raise ChromaSyncJobExecutionError(
                    str(result_summary.get("error") or "La sincronización Chroma falló")
                )
        except ChromaSyncJobExecutionError:
            raise
        except Exception as exc:
            raise ChromaSyncJobExecutionError(str(exc)[:400]) from exc

        progress(
            "chroma_synced",
            {
                "work_units": 4,
                "progress_total": 4,
                "eligible_items": result_summary.get("eligible_items", 0),
                "documents": result_summary.get("documents", 0),
                "inserted_or_updated": result_summary.get("inserted_or_updated", 0),
                "removed_stale": result_summary.get("removed_stale", 0),
            },
        )
        return {
            "target": "chromadb",
            "active_only": True,
            "erp_id": version.erp_id,
            "knowledge_version_id": str(version.id),
            "knowledge_version": version.knowledge_version,
            **result_summary,
        }

    @staticmethod
    def _version_id(parameters: dict[str, Any]) -> uuid.UUID:
        raw = parameters.get("knowledge_version_id")
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ChromaSyncJobExecutionError("knowledge_version_id inválido") from exc

    @staticmethod
    def _require_active_version(session, version_id, parameters):
        version = session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise ChromaSyncJobExecutionError("Versión de conocimiento no encontrada")
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise ChromaSyncJobExecutionError(
                "La versión dejó de ser ACTIVE; el job se canceló de forma segura"
            )
        expected = str(parameters.get("knowledge_version") or "")
        if expected and version.knowledge_version != expected:
            raise ChromaSyncJobExecutionError("La identidad de la versión activa cambió")
        return version
