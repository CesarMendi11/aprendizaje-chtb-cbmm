from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus, SyncStatus, SyncTarget
from src.database.repositories import ERPRepository, KnowledgeRepository, SyncJobRepository
from src.database.types import utcnow
from src.graph.projection_service import GraphProjectionService
from src.knowledge.canonical.privacy import sanitize_text

from .effective_knowledge_service import EffectiveKnowledgeService


@dataclass(frozen=True)
class Neo4jSyncResult:
    status: str
    summary: dict


class Neo4jSyncService:
    def __init__(self, session: Session, *, repository=None, projection=None):
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.erps = ERPRepository(session)
        self.jobs = SyncJobRepository(session)
        self.effective = EffectiveKnowledgeService(session)
        self.repository = repository
        self.projection = projection or GraphProjectionService()

    def prepare(self, *, erp_id=None, knowledge_version=None):
        version = self._version(erp_id, knowledge_version)
        erp = self.erps.get(version.erp_id)
        if not erp:
            raise LookupError("ERP de la versión no encontrado")
        job = self.jobs.get(version.id, SyncTarget.NEO4J)
        entries = self.effective.projection_for_sync(version_id=version.id)
        return self.projection.build_plan(
            entries,
            erp_id=erp.id,
            knowledge_version=version.knowledge_version,
            sync_job_id=str(job.id) if job else None,
            sync_job_status=str(job.status) if job else None,
            sync_job_attempt_count=job.attempt_count if job else None,
        )

    def run(
        self,
        *,
        erp_id=None,
        knowledge_version=None,
        batch_size=200,
        replace_version=False,
        allow_empty=False,
    ):
        version = self._version(erp_id, knowledge_version)
        plan = self.prepare(erp_id=version.erp_id, knowledge_version=version.knowledge_version)
        if not plan.eligible_items and not allow_empty:
            raise ValueError("No existen elementos approved/corrected; use --allow-empty")
        job = self.jobs.get(version.id, SyncTarget.NEO4J, for_update=True)
        if not job:
            raise LookupError("No existe SyncJob target=neo4j para la versión")
        if job.status == SyncStatus.RUNNING:
            raise ValueError("El SyncJob Neo4j ya está en ejecución")
        if not self.repository:
            raise ValueError("Repositorio Neo4j no configurado")
        job.status = SyncStatus.RUNNING
        job.attempt_count += 1
        job.started_at = utcnow()
        job.finished_at = None
        job.error_summary = None
        job.checkpoint = self._checkpoint(plan, batch_number=0)
        self.session.flush()
        try:
            self.repository.bootstrap()
            if replace_version:
                self.repository.replace_version(plan.erp_id, plan.knowledge_version)
            nodes = self.repository.upsert_nodes(plan.nodes, batch_size=batch_size)
            relationships = self.repository.upsert_relationships(
                plan.relationships, batch_size=batch_size
            )
            job.status = SyncStatus.SUCCEEDED
            job.finished_at = utcnow()
            job.checkpoint = self._checkpoint(
                plan,
                batch_number=max(1, (len(plan.nodes) + batch_size - 1) // batch_size),
                nodes=nodes,
                relationships=relationships,
            )
            self.session.flush()
            self.session.refresh(job)
            return Neo4jSyncResult("succeeded", self._final_summary(plan, job))
        except Exception as exc:
            message, _ = sanitize_text(str(exc), 400)
            job.status = SyncStatus.FAILED
            job.finished_at = utcnow()
            job.error_summary = message or "Error Neo4j sanitizado"
            self.session.flush()
            self.session.refresh(job)
            return Neo4jSyncResult(
                "failed",
                {**self._final_summary(plan, job), "error": job.error_summary},
            )

    def _version(self, erp_id, knowledge_version):
        if erp_id and knowledge_version:
            version = self.knowledge.get_version(erp_id, knowledge_version)
        elif erp_id:
            version = self.knowledge.get_active_version(erp_id)
        else:
            versions = [
                item
                for item in self.knowledge.list_versions()
                if item.status == KnowledgeVersionStatus.ACTIVE
                and (knowledge_version is None or item.knowledge_version == knowledge_version)
            ]
            if len(versions) != 1:
                raise ValueError("Indique --erp-id cuando no exista una única versión activa")
            version = versions[0]
        if not version:
            raise LookupError("Versión de conocimiento no encontrada")
        return version

    @staticmethod
    def _checkpoint(plan, *, batch_number, nodes=None, relationships=None):
        return {
            "eligible_items": plan.eligible_items,
            "projected_nodes": len(plan.nodes) if nodes is None else nodes,
            "projected_relationships": len(plan.relationships)
            if relationships is None
            else relationships,
            "skipped_relationships": plan.skipped_relationships,
            "batch_number": batch_number,
            "projection_hash": plan.projection_hash,
        }

    @staticmethod
    def _final_summary(plan, job):
        summary = plan.summary()
        summary["sync_job"] = {
            "id": str(job.id),
            "status": str(job.status),
            "attempt_count": job.attempt_count,
            "checkpoint": dict(job.checkpoint or {}),
        }
        summary["job_status"] = str(job.status)
        return summary
