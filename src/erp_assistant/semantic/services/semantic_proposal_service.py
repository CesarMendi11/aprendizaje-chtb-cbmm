from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from erp_assistant.persistence.postgres.enums import SemanticLifecycleOrigin, SemanticType
from erp_assistant.persistence.postgres.models import KnowledgeVersionRecord, SemanticProposal
from erp_assistant.persistence.postgres.repositories import (
    KnowledgeRepository,
    SemanticProposalRepository,
)
from erp_assistant.structural.canonical.enums import ReviewStatus

from .semantic_effective_payload_service import SemanticEffectivePayloadService
from .semantic_exceptions import (
    SemanticEntityTypeError,
    SemanticIdentityCollisionError,
    SemanticLifecycleIntegrityError,
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
    PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}

    def __init__(self, session: Session):
        self.session = session
        self.knowledge = KnowledgeRepository(session)
        self.proposals = SemanticProposalRepository(session)
        self.effective = SemanticEffectivePayloadService(session)

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
        return self._create(
            knowledge_version_id=knowledge_version_id,
            screen_knowledge_item_id=screen_knowledge_item_id,
            semantic_type=semantic_type,
            source_payload=source_payload,
            evidence_payload=evidence_payload,
            evidence_ids=evidence_ids,
            generation_model=generation_model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            generation_parameters=generation_parameters,
            lifecycle_origin=SemanticLifecycleOrigin.GENERATED,
            source_semantic_proposal_id=None,
            initial_review_status=ReviewStatus.PENDING_REVIEW,
        )

    def create_reinferred_pending_proposal(
        self,
        *,
        source_semantic_proposal_id: uuid.UUID | str,
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
        return self._create(
            knowledge_version_id=knowledge_version_id,
            screen_knowledge_item_id=screen_knowledge_item_id,
            semantic_type=semantic_type,
            source_payload=source_payload,
            evidence_payload=evidence_payload,
            evidence_ids=evidence_ids,
            generation_model=generation_model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            generation_parameters=generation_parameters,
            lifecycle_origin=SemanticLifecycleOrigin.REINFERRED,
            source_semantic_proposal_id=source_semantic_proposal_id,
            initial_review_status=ReviewStatus.PENDING_REVIEW,
        )

    def create_carried_forward_proposal(
        self,
        *,
        source_semantic_proposal_id: uuid.UUID | str,
        knowledge_version_id: uuid.UUID | str,
        screen_knowledge_item_id: uuid.UUID | str,
        semantic_type: SemanticType | str,
        evidence_payload: dict[str, Any] | ValidatedSemanticEvidenceSnapshot,
        evidence_ids: list[str],
        generation_model: str,
        prompt_version: str,
        prompt_hash: str,
        generation_parameters: dict[str, Any],
    ) -> SemanticProposal:
        version_id = self._uuid(knowledge_version_id, "knowledge_version_id")
        screen_id = self._uuid(screen_knowledge_item_id, "screen_knowledge_item_id")
        semantic_kind = self._semantic_type(semantic_type)
        version, screen = self._target_context(version_id, screen_id)
        lineage = self._validated_lineage(
            source_semantic_proposal_id,
            target_version=version,
            target_screen=screen,
            semantic_type=semantic_kind,
        )
        return self._create(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=semantic_kind,
            source_payload=lineage["effective_payload"],
            evidence_payload=evidence_payload,
            evidence_ids=evidence_ids,
            generation_model=generation_model,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            generation_parameters=generation_parameters,
            lifecycle_origin=SemanticLifecycleOrigin.CARRIED_FORWARD,
            source_semantic_proposal_id=lineage["proposal"].id,
            initial_review_status=lineage["source_review_status"],
            prevalidated_lineage=lineage,
        )

    def _create(
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
        lifecycle_origin: SemanticLifecycleOrigin,
        source_semantic_proposal_id: uuid.UUID | str | None,
        initial_review_status: ReviewStatus,
        prevalidated_lineage: dict[str, Any] | None = None,
    ) -> SemanticProposal:
        version_id = self._uuid(knowledge_version_id, "knowledge_version_id")
        screen_id = self._uuid(screen_knowledge_item_id, "screen_knowledge_item_id")
        version, screen = self._target_context(version_id, screen_id)
        semantic_kind = self._semantic_type(semantic_type)
        origin = SemanticLifecycleOrigin(lifecycle_origin)

        if origin == SemanticLifecycleOrigin.GENERATED:
            if source_semantic_proposal_id is not None or prevalidated_lineage is not None:
                raise SemanticLifecycleIntegrityError(
                    "Una propuesta generated no puede declarar provenance semántica"
                )
            lineage = None
        else:
            lineage = prevalidated_lineage or self._validated_lineage(
                source_semantic_proposal_id,
                target_version=version,
                target_screen=screen,
                semantic_type=semantic_kind,
            )

        if origin == SemanticLifecycleOrigin.CARRIED_FORWARD:
            if lineage is None or source_payload != lineage["effective_payload"]:
                raise SemanticLifecycleIntegrityError(
                    "El carry-forward debe materializar exactamente el payload efectivo source"
                )
            if initial_review_status != lineage["source_review_status"]:
                raise SemanticLifecycleIntegrityError(
                    "El carry-forward debe conservar el estado publicable source"
                )
        elif origin == SemanticLifecycleOrigin.REINFERRED:
            if initial_review_status != ReviewStatus.PENDING_REVIEW:
                raise SemanticLifecycleIntegrityError(
                    "Una reinferencia debe iniciar en pending_review"
                )

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
            evidence, evidence_hash, snapshot_evidence_ids = semantic_evidence_snapshot_values(
                evidence_payload
            )
            if snapshot_evidence_ids != normalized_evidence_ids:
                raise SemanticPayloadError("evidence_ids no coincide con el snapshot validado")
        else:
            evidence = validate_semantic_payload(evidence_payload, field="evidence_payload")
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
        lineage_values = self._lineage_values(lineage, origin)
        expected = {
            "semantic_id": semantic_id,
            "source_payload": source,
            "source_content_hash": source_hash,
            "evidence_payload": evidence,
            "evidence_ids": normalized_evidence_ids,
            "generation_parameters": parameters,
            "prompt_version": prompt,
            "lifecycle_origin": origin,
            **lineage_values,
        }
        existing = self.proposals.get_by_generation_identity(**identity)
        if existing is not None:
            return self._verify_existing(existing, **expected)
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
            lifecycle_origin=origin,
            current_review_status=initial_review_status,
            review_revision=0,
            **lineage_values,
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
            return self._verify_existing(existing, **expected)
        return proposal

    def _target_context(self, version_id: uuid.UUID, screen_id: uuid.UUID):
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
        if screen.current_review_status not in self.PUBLISHABLE:
            raise SemanticScreenReviewError("La pantalla no tiene revisión publicable")
        return version, screen

    def _validated_lineage(
        self,
        source_semantic_proposal_id,
        *,
        target_version,
        target_screen,
        semantic_type: SemanticType,
    ) -> dict[str, Any]:
        source_id = self._uuid(
            source_semantic_proposal_id,
            "source_semantic_proposal_id",
        )
        source = self.proposals.get_by_id(source_id, for_update=True)
        if source is None:
            raise SemanticLifecycleIntegrityError("La propuesta source no existe")
        if source.knowledge_version_id == target_version.id:
            raise SemanticLifecycleIntegrityError(
                "La propuesta source debe pertenecer a otra KnowledgeVersion"
            )
        if source.semantic_type != semantic_type:
            raise SemanticLifecycleIntegrityError("semantic_type source/target no coincide")
        if source.current_review_status not in self.PUBLISHABLE:
            raise SemanticLifecycleIntegrityError("La propuesta source no es publicable")
        source_screen = source.screen_knowledge_item
        if source_screen is None or source_screen.canonical_id != target_screen.canonical_id:
            raise SemanticLifecycleIntegrityError(
                "La propuesta source pertenece a otra pantalla canónica"
            )
        effective = self.effective.publishable_payload(source.id)
        if not isinstance(effective, dict):
            raise SemanticLifecycleIntegrityError(
                "La propuesta source no tiene payload efectivo publicable"
            )
        effective_hash = canonical_json_hash(effective)
        return {
            "proposal": source,
            "effective_payload": effective,
            "source_knowledge_version_id": source.knowledge_version_id,
            "source_review_status": source.current_review_status,
            "source_review_revision": int(source.review_revision),
            "source_effective_content_hash": effective_hash,
        }

    @staticmethod
    def _lineage_values(
        lineage: dict[str, Any] | None,
        origin: SemanticLifecycleOrigin,
    ) -> dict[str, Any]:
        if origin == SemanticLifecycleOrigin.GENERATED:
            return {
                "source_semantic_proposal_id": None,
                "source_knowledge_version_id": None,
                "source_review_status": None,
                "source_review_revision": None,
                "source_effective_content_hash": None,
            }
        if lineage is None:
            raise SemanticLifecycleIntegrityError("Falta provenance semántica derivada")
        return {
            "source_semantic_proposal_id": lineage["proposal"].id,
            "source_knowledge_version_id": lineage["source_knowledge_version_id"],
            "source_review_status": lineage["source_review_status"],
            "source_review_revision": lineage["source_review_revision"],
            "source_effective_content_hash": lineage["source_effective_content_hash"],
        }

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
    def _semantic_type(value: SemanticType | str) -> SemanticType:
        try:
            return SemanticType(value)
        except ValueError as exc:
            raise SemanticPayloadError("semantic_type no soportado") from exc

    @staticmethod
    def _required_text(value: Any, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SemanticPayloadError(f"{field} no puede estar vacío")
        clean = value.strip()
        if len(clean) > limit:
            raise SemanticPayloadError(f"{field} excede el tamaño permitido")
        return clean

    @staticmethod
    def _uuid(value: uuid.UUID | str | None, field: str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise SemanticPayloadError(f"{field} no es un UUID válido") from exc
