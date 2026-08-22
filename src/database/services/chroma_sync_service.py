from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import KnowledgeVersionStatus, SyncStatus, SyncTarget
from src.database.models import KnowledgeVersionRecord
from src.database.repositories import ERPRepository, KnowledgeRepository, SyncJobRepository
from src.database.types import utcnow
from src.knowledge.canonical.privacy import sanitize_text
from src.vectorstore import collection_name, document_id

from .effective_knowledge_service import EffectiveKnowledgeService
from .projection_replacement_service import ProjectionReplacementService

TYPE_NAMES = {
    "erp_system": "ERP",
    "module": "Módulo",
    "screen": "Pantalla",
    "ui_state": "Estado de interfaz",
    "field": "Campo",
    "control": "Control",
    "table": "Tabla",
    "table_column": "Columna de tabla",
    "link": "Enlace",
    "event": "Evento",
    "transition": "Transición",
}
LABEL_KEYS = {
    "erp_system": ("name",),
    "module": ("name",),
    "screen": ("title",),
    "ui_state": ("title",),
    "field": ("label", "name"),
    "control": ("label",),
    "table": ("name",),
    "table_column": ("name",),
    "link": ("label",),
    "event": ("label",),
    "transition": ("category",),
}


@dataclass(frozen=True)
class ChromaDocument:
    id: str
    text: str
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class ChromaSyncResult:
    status: str
    summary: dict[str, Any]


class SafeDocumentBuilder:
    def build(self, entries, *, erp, knowledge_version):
        payloads = {entry["canonical_id"]: entry["payload"] for entry in entries}
        types = {entry["canonical_id"]: entry["entity_type"] for entry in entries}
        documents, skipped = [], Counter()
        for entry in entries:
            try:
                documents.append(self._one(entry, payloads, types, erp, knowledge_version))
            except ValueError as exc:
                skipped[str(exc)] += 1
        return documents, dict(sorted(skipped.items()))

    def _one(self, entry, payloads, types, erp, knowledge_version):
        payload = entry["payload"]
        entity_type = entry["entity_type"]
        label = self._label(entity_type, payload)
        if not label:
            raise ValueError("missing_safe_label")
        screen_id = payload.get("screen_id") or (
            entry["canonical_id"] if entity_type == "screen" else None
        )
        screen = payloads.get(screen_id, {})
        module = payloads.get(screen.get("module_id") or payload.get("module_id"), {})
        table = payloads.get(payload.get("table_id"), {})
        parent_id = entry.get("parent_canonical_id")
        parent = payloads.get(parent_id, {})
        route = payload.get("route") or screen.get("route") or entry.get("route")
        lines = [
            f"Tipo: {TYPE_NAMES.get(entity_type, entity_type)}",
            f"Nombre: {label}",
            f"ERP: {erp.name}",
        ]
        self._append(lines, "Módulo", self._label("module", module))
        self._append(lines, "Pantalla", self._label("screen", screen))
        self._append(lines, "Ruta", route)
        self._append(lines, "Entidad padre", self._label(types.get(parent_id, ""), parent))
        self._append(lines, "Tabla asociada", self._label("table", table))
        self._append(
            lines,
            "Estado origen",
            self._label("ui_state", payloads.get(payload.get("source_state_id"), {})),
        )
        self._append(
            lines,
            "Estado destino",
            self._label("ui_state", payloads.get(payload.get("target_state_id"), {})),
        )
        description = payload.get("description")
        if description:
            self._append(lines, "Descripción", description)
        else:
            self._append(
                lines,
                "Descripción estructural",
                f"{TYPE_NAMES.get(entity_type, 'Entidad')} disponible en el ERP.",
            )
        metadata = {
            "erp_id": erp.id,
            "knowledge_version": knowledge_version,
            "canonical_id": entry["canonical_id"],
            "entity_type": entity_type,
            "review_status": entry["review_status"],
            "content_hash": entry["content_hash"],
        }
        optional = {
            "parent_canonical_id": parent_id,
            "screen_id": screen_id,
            "screen_route": route,
        }
        metadata.update({key: str(value) for key, value in optional.items() if value})
        return ChromaDocument(
            id=document_id(erp.id, knowledge_version, entry["canonical_id"]),
            text="\n".join(lines),
            metadata=metadata,
        )

    @staticmethod
    def _label(entity_type, payload):
        if not isinstance(payload, dict):
            return ""
        for key in LABEL_KEYS.get(entity_type, ("label", "name", "title")):
            clean, detections = sanitize_text(payload.get(key), 240)
            if clean and not detections:
                return clean
        return ""

    @staticmethod
    def _append(lines, key, value):
        clean, detections = sanitize_text(value, 500)
        if clean and not detections:
            lines.append(f"{key}: {clean}")


class ChromaSyncService:
    def __init__(self, session: Session, *, repository=None, embeddings=None, builder=None):
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.erps = ERPRepository(session)
        self.jobs = SyncJobRepository(session)
        self.effective = EffectiveKnowledgeService(session)
        self.repository = repository
        self.embeddings = embeddings
        self.builder = builder or SafeDocumentBuilder()

    def resolve_version(self, *, erp_id=None, knowledge_version=None):
        """Resolve the governed knowledge version without preparing projection documents."""
        return self._version(erp_id, knowledge_version)

    def prepare(self, *, erp_id=None, knowledge_version=None):
        version = self.resolve_version(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        erp = self.erps.get(version.erp_id)
        if not erp:
            raise LookupError("ERP de la versión no encontrado")
        items = self.effective.list_approved(version_id=version.id)
        entries = []
        for item in items:
            entries.append(
                {
                    "canonical_id": item.canonical_id,
                    "entity_type": item.entity_type,
                    "parent_canonical_id": item.parent_canonical_id,
                    "route": item.route,
                    "content_hash": item.content_hash,
                    "review_status": str(item.current_review_status),
                    "payload": self.effective.describe(item.id)["effective_payload"],
                }
            )
        documents, reasons = self.builder.build(
            entries, erp=erp, knowledge_version=version.knowledge_version
        )
        return version, documents, self._summary(version, items, documents, reasons)

    def run(self, *, erp_id=None, knowledge_version=None):
        version = self._version(erp_id, knowledge_version, for_update=True)
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise ValueError(
                "La versión dejó de ser ACTIVE; la proyección ChromaDB se canceló de forma segura"
            )
        lineage = ProjectionReplacementService(self.session).resolve(version.id)
        if not self.repository or not self.embeddings:
            raise ValueError("ChromaDB y cliente de embeddings deben estar configurados")
        job = self.jobs.get(version.id, SyncTarget.CHROMADB, for_update=True)
        if not job:
            raise LookupError("No existe SyncJob target=chromadb para la versión")
        if job.status == SyncStatus.RUNNING:
            raise ValueError("El SyncJob ChromaDB ya está en ejecución")
        version, documents, summary = self.prepare(
            erp_id=version.erp_id, knowledge_version=version.knowledge_version
        )
        job.status, job.started_at, job.finished_at = SyncStatus.RUNNING, utcnow(), None
        job.attempt_count += 1
        job.error_summary = None
        job.checkpoint = {
            "eligible_items": summary["eligible_items"],
            "documents": summary["documents"],
            "phase": "prepared",
        }
        self.session.flush()
        try:
            vectors = self.embeddings.embed([document.text for document in documents])
            changed, removed = self.repository.sync(
                documents,
                vectors,
                erp_id=version.erp_id,
                knowledge_version=version.knowledge_version,
            )
            removed_previous_version = 0
            if lineage.previous_active_knowledge_version is not None:
                removed_previous_version = self.repository.delete_version(
                    erp_id=lineage.erp_id,
                    knowledge_version=lineage.previous_active_knowledge_version,
                )
            summary.update(
                {
                    "embedding_model": self.embeddings.model,
                    "embedding_dimensions": self.embeddings.dimensions,
                    "inserted_or_updated": changed,
                    "removed_stale": removed,
                    "previous_version_id": (
                        str(lineage.previous_active_version_id)
                        if lineage.previous_active_version_id is not None
                        else None
                    ),
                    "previous_knowledge_version": lineage.previous_active_knowledge_version,
                    "removed_previous_version": removed_previous_version,
                }
            )
            job.status, job.finished_at = SyncStatus.SUCCEEDED, utcnow()
            job.error_summary = None
            job.checkpoint = {
                key: summary[key]
                for key in (
                    "eligible_items",
                    "documents",
                    "embedding_dimensions",
                    "inserted_or_updated",
                    "removed_stale",
                    "previous_version_id",
                    "previous_knowledge_version",
                    "removed_previous_version",
                )
            }
            self.session.flush()
            summary["sync_job"] = self._job(job)
            return ChromaSyncResult("succeeded", summary)
        except Exception as exc:
            clean, _ = sanitize_text(exc, 400)
            job.status, job.finished_at = SyncStatus.FAILED, utcnow()
            job.error_summary = clean or "Error ChromaDB sanitizado"
            self.session.flush()
            summary.update({"sync_job": self._job(job), "error": job.error_summary})
            return ChromaSyncResult("failed", summary)

    def _version(self, erp_id, knowledge_version, *, for_update=False):
        query = select(KnowledgeVersionRecord)
        if erp_id and knowledge_version:
            query = query.where(
                KnowledgeVersionRecord.erp_id == erp_id,
                KnowledgeVersionRecord.knowledge_version == knowledge_version,
            )
        elif erp_id:
            query = query.where(
                KnowledgeVersionRecord.erp_id == erp_id,
                KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
            )
        else:
            query = query.where(KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE)
            if knowledge_version is not None:
                query = query.where(
                    KnowledgeVersionRecord.knowledge_version == knowledge_version
                )
        if for_update:
            query = query.with_for_update()
        candidates = list(self.session.scalars(query))
        if not erp_id and len(candidates) != 1:
            raise ValueError("Indique --erp-id cuando no exista una única versión activa")
        version = candidates[0] if len(candidates) == 1 else None
        if not version:
            raise LookupError("Versión de conocimiento no encontrada")
        return version

    @staticmethod
    def _summary(version, items, documents, reasons):
        return {
            "erp_id": version.erp_id,
            "knowledge_version": version.knowledge_version,
            "eligible_items": len(items),
            "documents": len(documents),
            "documents_by_type": dict(
                sorted(Counter(d.metadata["entity_type"] for d in documents).items())
            ),
            "embedding_model": None,
            "embedding_dimensions": None,
            "collection_name": collection_name(),
            "inserted_or_updated": 0,
            "removed_stale": 0,
            "previous_version_id": None,
            "previous_knowledge_version": None,
            "removed_previous_version": 0,
            "skipped": sum(reasons.values()),
            "skipped_reasons": reasons,
        }

    @staticmethod
    def _job(job):
        return {
            "id": str(job.id),
            "status": str(job.status),
            "attempt_count": job.attempt_count,
            "checkpoint": dict(job.checkpoint or {}),
        }
