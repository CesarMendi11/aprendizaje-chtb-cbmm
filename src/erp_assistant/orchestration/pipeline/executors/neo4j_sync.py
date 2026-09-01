from __future__ import annotations

import uuid
from typing import Any

from erp_assistant.config.neo4j_settings import Neo4jSettings
from erp_assistant.persistence.postgres.enums import (
    KnowledgeVersionStatus,
    PipelineJobScope,
    SyncTarget,
)
from erp_assistant.persistence.postgres.models import KnowledgeVersionRecord
from erp_assistant.projections.neo4j.client import Neo4jClient
from erp_assistant.projections.neo4j.repository import Neo4jRepository
from erp_assistant.projections.neo4j.sync_service import Neo4jSyncService
from erp_assistant.structural.canonical.privacy import sanitize_text

from ..projection_sync_state import fail_preflight_sync, sync_attempt_count


class Neo4jSyncJobExecutionError(RuntimeError):
    pass


class Neo4jSyncJobExecutor:
    """Project only the version that was active when the controlled job was queued."""

    def __init__(self, session_factory, *, repository_factory=None):
        self.session_factory = session_factory
        self.repository_factory = repository_factory or self._default_repository

    @staticmethod
    def _default_repository():
        return Neo4jRepository(Neo4jClient(Neo4jSettings()))

    def execute(self, *, job_id, scope, target, parameters, progress):
        if scope != PipelineJobScope.VERSION:
            raise Neo4jSyncJobExecutionError("Neo4j sync requiere scope=version")
        version_id = self._version_id(parameters)
        batch_size = int(parameters.get("batch_size", 200))
        replace_version = bool(parameters.get("replace_version", False))
        if batch_size < 1 or batch_size > 2000:
            raise Neo4jSyncJobExecutionError("batch_size fuera del rango permitido")

        progress(
            "validating_active_version",
            {"work_units": 1, "progress_total": 4, "knowledge_version_id": str(version_id)},
        )
        attempt_count_before = sync_attempt_count(
            self.session_factory, version_id, SyncTarget.NEO4J
        )
        try:
            with self.session_factory() as session:
                version = self._require_active_version(session, version_id, parameters)
                service = Neo4jSyncService(session)
                plan = service.prepare(
                    erp_id=version.erp_id,
                    knowledge_version=version.knowledge_version,
                )
                plan_summary = plan.summary()
        except Neo4jSyncJobExecutionError:
            raise
        except Exception as exc:
            fail_preflight_sync(
                self.session_factory,
                version_id=version_id,
                target=SyncTarget.NEO4J,
                attempt_count_before=attempt_count_before,
                error=exc,
            )
            clean, _ = sanitize_text(str(exc), 400)
            raise Neo4jSyncJobExecutionError(clean or "Error Neo4j sanitizado") from exc

        progress(
            "projection_planned",
            {
                "work_units": 2,
                "progress_total": 4,
                "eligible_items": plan_summary["eligible_items"],
                "nodes": plan_summary["nodes"],
                "relationships": plan_summary["relationships"],
            },
        )

        repository = None
        try:
            repository = self.repository_factory()
            progress(
                "syncing_neo4j",
                {
                    "work_units": 3,
                    "progress_total": 4,
                    "eligible_items": plan_summary["eligible_items"],
                },
            )
            with self.session_factory.begin() as session:
                version = self._require_active_version(session, version_id, parameters)
                result = Neo4jSyncService(session, repository=repository).run(
                    erp_id=version.erp_id,
                    knowledge_version=version.knowledge_version,
                    batch_size=batch_size,
                    replace_version=replace_version,
                    allow_empty=False,
                )
                summary = dict(result.summary)
            if result.status != "succeeded":
                raise Neo4jSyncJobExecutionError(
                    str(summary.get("error") or "La sincronización Neo4j falló")
                )
        except Neo4jSyncJobExecutionError:
            raise
        except Exception as exc:
            settings = Neo4jSettings()
            message = str(exc)
            if settings.password:
                message = message.replace(settings.password, "[redacted]")
            clean, _ = sanitize_text(message, 400)
            raise Neo4jSyncJobExecutionError(clean or "Error Neo4j sanitizado") from exc
        finally:
            client = getattr(repository, "client", None)
            close = getattr(client, "close", None)
            if callable(close):
                close()

        progress(
            "neo4j_synced",
            {
                "work_units": 4,
                "progress_total": 4,
                "eligible_items": summary.get("eligible_items", 0),
                "nodes": summary.get("nodes", 0),
                "relationships": summary.get("relationships", 0),
            },
        )
        return {
            "target": "neo4j",
            "active_only": True,
            "erp_id": version.erp_id,
            "knowledge_version_id": str(version.id),
            "knowledge_version": version.knowledge_version,
            "replace_version": replace_version,
            **summary,
        }

    @staticmethod
    def _version_id(parameters: dict[str, Any]) -> uuid.UUID:
        raw = parameters.get("knowledge_version_id")
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError, AttributeError) as exc:
            raise Neo4jSyncJobExecutionError("knowledge_version_id inválido") from exc

    @staticmethod
    def _require_active_version(session, version_id, parameters):
        version = session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise Neo4jSyncJobExecutionError("Versión de conocimiento no encontrada")
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise Neo4jSyncJobExecutionError(
                "La versión dejó de ser ACTIVE; el job se canceló de forma segura"
            )
        expected = str(parameters.get("knowledge_version") or "")
        if expected and version.knowledge_version != expected:
            raise Neo4jSyncJobExecutionError("La identidad de la versión activa cambió")
        return version
