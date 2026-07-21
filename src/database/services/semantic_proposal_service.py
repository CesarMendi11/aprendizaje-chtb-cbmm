from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.database.enums import SemanticType
from src.database.models import KnowledgeVersionRecord, SemanticProposal
from src.database.repositories import KnowledgeRepository, SemanticProposalRepository
from src.knowledge.canonical.enums import ReviewStatus

from .semantic_exceptions import (
    SemanticEntityTypeError,
    SemanticIdentityCollisionError,
    SemanticPayloadError,
    SemanticScreenNotFoundError,
    SemanticScreenReviewError,
    SemanticVersionMismatchError,
)
from .semantic_payloads import (
    ValidatedSemanticEvidenceSnapshot,
    canonical_json_hash,
    normalize_evidence_ids,
    semantic_evidence_hash,
    semantic_evidence_snapshot_values,
    validate_semantic_payload,
    validate_sha256,
)


class SemanticProposalService:
    def __init__(self, session: Session):
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.proposals = SemanticProposalRepository(session)

    def create_pending_proposal(
        self,
        *,
        knowledge_version_id: uuid.UUID | str,
        screen_knowledge_item_id: uuid.UUID | str,
        semantic_type: SemanticType | str,
        source_payload: dict[str, Any],
        evidence_payload: dict[str, Any] | ValidatedSemanticEvidenceSnapshot,
        evidence_ids: list[str],
        generation_model: str,
        prompt_version: str,
        prompt_hash: str,
        generation_parameters: dict[str, Any],
    ) -> SemanticProposal:
        version_id = self._uuid(knowledge_version_id, "knowledge_version_id")
        screen_id = self._uuid(screen_knowledge_item_id, "screen_knowledge_item_id")
        version = self.session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise SemanticVersionMismatchError("La versión de conocimiento no existe")
        screen = self.knowledge.get_item(screen_id)
        if screen is None:
            raise SemanticScreenNotFoundError("La pantalla no existe")
        if screen.entity_type != "screen":
            raise SemanticEntityTypeError("El KnowledgeItem no es una pantalla")
        if screen.knowledge_version_id != version.id:
            raise SemanticVersionMismatchError("La pantalla pertenece a otra versión")
        if screen.current_review_status not in {
            ReviewStatus.APPROVED,
            ReviewStatus.CORRECTED,
        }:
            raise SemanticScreenReviewError("La pantalla no tiene revisión publicable")
        try:
            semantic_kind = SemanticType(semantic_type)
        except ValueError as exc:
            raise SemanticPayloadError("semantic_type no soportado") from exc
        model = self._required_text(generation_model, "generation_model", 120)
        prompt = self._required_text(prompt_version, "prompt_version", 120)
        prompt_digest = validate_sha256(prompt_hash, field="prompt_hash")
        source = validate_semantic_payload(
            source_payload,
            field="source_payload",
            require_purpose_summary=True,
        )
        normalized_evidence_ids = normalize_evidence_ids(evidence_ids)
        if isinstance(evidence_payload, ValidatedSemanticEvidenceSnapshot):
            evidence, evidence_hash, snapshot_evidence_ids = (
                semantic_evidence_snapshot_values(evidence_payload)
            )
            if snapshot_evidence_ids != normalized_evidence_ids:
                raise SemanticPayloadError("evidence_ids no coincide con el snapshot validado")
        else:
            evidence = validate_semantic_payload(
                evidence_payload, field="evidence_payload"
            )
            evidence_hash = semantic_evidence_hash(evidence, normalized_evidence_ids)
        parameters = validate_semantic_payload(
            generation_parameters,
            field="generation_parameters",
            allow_empty=True,
        )
        source_hash = canonical_json_hash(source)
        parameters_hash = canonical_json_hash(parameters)
        identity = {
            "knowledge_version_id": version.id,
            "screen_knowledge_item_id": screen.id,
            "semantic_type": semantic_kind,
            "evidence_hash": evidence_hash,
            "prompt_hash": prompt_digest,
            "generation_model": model,
            "generation_parameters_hash": parameters_hash,
        }
        serialized_identity = {
            **identity,
            "knowledge_version_id": str(version.id),
            "screen_knowledge_item_id": str(screen.id),
            "semantic_type": str(semantic_kind),
        }
        semantic_id = f"semantic:{canonical_json_hash(serialized_identity)}"
        existing = self.proposals.get_by_generation_identity(**identity)
        if existing is not None:
            return self._verify_existing(
                existing,
                semantic_id=semantic_id,
                source_payload=source,
                source_content_hash=source_hash,
                evidence_payload=evidence,
                evidence_ids=normalized_evidence_ids,
                generation_parameters=parameters,
                prompt_version=prompt,
            )
        proposal = SemanticProposal(
            semantic_id=semantic_id,
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=semantic_kind,
            source_payload=source,
            source_content_hash=source_hash,
            evidence_payload=evidence,
            evidence_hash=evidence_hash,
            evidence_ids=normalized_evidence_ids,
            generation_model=model,
            prompt_version=prompt,
            prompt_hash=prompt_digest,
            generation_parameters=parameters,
            generation_parameters_hash=parameters_hash,
        )
        try:
            with self.session.begin_nested():
                self.proposals.add(proposal)
                self.session.flush()
        except IntegrityError as exc:
            existing = self.proposals.get_by_generation_identity(**identity)
            if existing is None:
                existing = self.proposals.get_by_semantic_id(semantic_id)
            if existing is None:
                raise SemanticIdentityCollisionError(
                    "La identidad semántica colisionó sin una propuesta recuperable"
                ) from exc
            return self._verify_existing(
                existing,
                semantic_id=semantic_id,
                source_payload=source,
                source_content_hash=source_hash,
                evidence_payload=evidence,
                evidence_ids=normalized_evidence_ids,
                generation_parameters=parameters,
                prompt_version=prompt,
            )
        return proposal

    @staticmethod
    def _verify_existing(existing: SemanticProposal, **expected) -> SemanticProposal:
        incompatible = [
            field for field, value in expected.items() if getattr(existing, field) != value
        ]
        if incompatible:
            raise SemanticIdentityCollisionError(
                "La propuesta existente no coincide con su identidad de generación: "
                + ", ".join(sorted(incompatible))
            )
        return existing

    @staticmethod
    def _required_text(value: Any, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SemanticPayloadError(f"{field} no puede estar vacío")
        clean = value.strip()
        if len(clean) > limit:
            raise SemanticPayloadError(f"{field} excede el tamaño permitido")
        return clean

    @staticmethod
    def _uuid(value: uuid.UUID | str, field: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise SemanticPayloadError(f"{field} no es un UUID válido") from exc
