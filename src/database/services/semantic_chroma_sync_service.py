from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from src.analysis.evidence.screen_evidence_builder import (
    ScreenEvidenceBuilder,
    ScreenEvidenceError,
)
from src.analysis.eligibility import evaluate_screen_semantic_eligibility
from src.database.enums import KnowledgeVersionStatus
from src.database.models import KnowledgeVersionRecord, SemanticProposal
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import sanitize_text
from src.vectorstore import semantic_collection_name, semantic_document_id

from .semantic_effective_payload_service import SemanticEffectivePayloadService

PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


@dataclass(frozen=True)
class SemanticChromaDocument:
    id: str
    text: str
    metadata: dict[str, str | int | float | bool]


@dataclass(frozen=True)
class SemanticChromaSyncResult:
    status: str
    summary: dict[str, Any]


class SemanticSafeDocumentBuilder:
    def build(self, proposal, payload, *, version, erp, screen):
        if not isinstance(payload, dict):
            raise ValueError("invalid_publishable_payload")
        semantic_type = str(proposal.semantic_type)
        if semantic_type != "screen_purpose":
            raise ValueError("unsupported_semantic_type")

        title = self._safe(screen.title or screen.source_payload.get("title"), 240)
        route = self._safe(screen.route or screen.source_payload.get("route"), 500)
        purpose = self._safe(payload.get("purpose_summary"), 1000)
        if not title or not purpose:
            raise ValueError("missing_safe_semantic_text")

        lines = [
            "Tipo: Semántica aprobada",
            "Semántica: Propósito de pantalla",
            f"Pantalla: {title}",
        ]
        if route:
            lines.append(f"Ruta: {route}")
        lines.append(f"Propósito: {purpose}")

        capabilities = payload.get("supported_capabilities") or []
        safe_capabilities = []
        for capability in capabilities:
            if not isinstance(capability, dict):
                continue
            statement = self._safe(capability.get("statement"), 1000)
            if statement:
                safe_capabilities.append(statement)
                lines.append(f"Capacidad: {statement}")

        metadata = {
            "erp_id": erp.id,
            "knowledge_version": version.knowledge_version,
            # canonical_id remains the structural seed used by the hybrid graph.
            "canonical_id": screen.canonical_id,
            "screen_id": screen.canonical_id,
            "semantic_id": proposal.semantic_id,
            "semantic_type": semantic_type,
            "entity_type": "semantic_screen_purpose",
            "review_status": str(proposal.current_review_status),
            "review_revision": int(proposal.review_revision),
            "evidence_hash": proposal.evidence_hash,
            "safe_label": title,
            "document_kind": "semantic",
            "capabilities_count": len(safe_capabilities),
        }
        if route:
            metadata["screen_route"] = route
        return SemanticChromaDocument(
            id=semantic_document_id(erp.id, version.knowledge_version, proposal.semantic_id),
            text="\n".join(lines),
            metadata=metadata,
        )

    @staticmethod
    def _safe(value, limit):
        clean, detections = sanitize_text(value, limit)
        return clean if clean and not detections else ""


class SemanticChromaSyncService:
    """Prepare and publish only current, human-approved semantic proposals."""

    def __init__(
        self,
        session: Session,
        *,
        repository=None,
        embeddings=None,
        builder=None,
        evidence_builder=None,
    ):
        self.session = session
        self.repository = repository
        self.embeddings = embeddings
        self.builder = builder or SemanticSafeDocumentBuilder()
        self.evidence_builder = evidence_builder or ScreenEvidenceBuilder(session)
        self.effective = SemanticEffectivePayloadService(session)

    def prepare(self, *, erp_id=None, knowledge_version=None):
        version = self._version(erp_id, knowledge_version)
        erp = version.erp
        proposals = list(
            self.session.scalars(
                select(SemanticProposal)
                .where(
                    SemanticProposal.knowledge_version_id == version.id,
                    SemanticProposal.current_review_status.in_(sorted(PUBLISHABLE)),
                )
                .options(joinedload(SemanticProposal.screen_knowledge_item))
                .order_by(
                    SemanticProposal.updated_at.desc(),
                    SemanticProposal.created_at.desc(),
                    SemanticProposal.semantic_id.desc(),
                )
            )
        )

        documents = []
        skipped = Counter()
        selected_keys = set()
        for proposal in proposals:
            key = (proposal.screen_knowledge_item_id, str(proposal.semantic_type))
            if key in selected_keys:
                skipped["superseded_publishable_proposal"] += 1
                continue
            screen = proposal.screen_knowledge_item
            if (
                screen is None
                or screen.knowledge_version_id != version.id
                or screen.current_review_status not in PUBLISHABLE
            ):
                skipped["screen_not_publishable"] += 1
                continue
            try:
                package = self.evidence_builder.build(version.id, screen.id)
            except ScreenEvidenceError:
                skipped["current_evidence_unavailable"] += 1
                continue
            if not evaluate_screen_semantic_eligibility(package).eligible:
                skipped["current_evidence_ineligible"] += 1
                continue
            if (
                proposal.evidence_hash != package.evidence_hash
                or list(proposal.evidence_ids) != list(package.evidence_ids)
            ):
                skipped["stale_evidence"] += 1
                continue
            payload = self.effective.publishable_payload(proposal.id)
            if payload is None:
                skipped["proposal_not_publishable"] += 1
                continue
            try:
                document = self.builder.build(
                    proposal,
                    payload,
                    version=version,
                    erp=erp,
                    screen=screen,
                )
            except ValueError as exc:
                skipped[str(exc)] += 1
                continue
            documents.append(document)
            selected_keys.add(key)

        summary = {
            "erp_id": version.erp_id,
            "knowledge_version": version.knowledge_version,
            "publishable_proposals": len(proposals),
            "documents": len(documents),
            "documents_by_type": dict(
                sorted(Counter(d.metadata["semantic_type"] for d in documents).items())
            ),
            "collection_name": semantic_collection_name(),
            "embedding_model": None,
            "embedding_dimensions": None,
            "inserted_or_updated": 0,
            "removed_stale": 0,
            "skipped": sum(skipped.values()),
            "skipped_reasons": dict(sorted(skipped.items())),
        }
        return version, documents, summary

    def run(self, *, erp_id=None, knowledge_version=None):
        version, documents, summary = self.prepare(
            erp_id=erp_id, knowledge_version=knowledge_version
        )
        if not self.repository or not self.embeddings:
            raise ValueError("ChromaDB semántico y cliente de embeddings deben estar configurados")
        vectors = (
            self.embeddings.embed([document.text for document in documents])
            if documents
            else []
        )
        changed, removed = self.repository.sync(
            documents,
            vectors,
            erp_id=version.erp_id,
            knowledge_version=version.knowledge_version,
        )
        summary.update(
            {
                "embedding_model": self.embeddings.model,
                "embedding_dimensions": self.embeddings.dimensions,
                "inserted_or_updated": changed,
                "removed_stale": removed,
            }
        )
        return SemanticChromaSyncResult("succeeded", summary)

    def _version(self, erp_id, knowledge_version):
        query = select(KnowledgeVersionRecord).options(joinedload(KnowledgeVersionRecord.erp))
        if erp_id:
            query = query.where(KnowledgeVersionRecord.erp_id == erp_id)
        if knowledge_version:
            query = query.where(KnowledgeVersionRecord.knowledge_version == knowledge_version)
        query = query.where(KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE)
        candidates = list(self.session.scalars(query))
        if len(candidates) != 1:
            raise ValueError("Se requiere exactamente una versión ACTIVE para sincronizar semántica")
        return candidates[0]
