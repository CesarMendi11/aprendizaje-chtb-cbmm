from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from src.analysis.evidence import ScreenEvidenceBuilder
from src.analysis.generation.screen_purpose_service import ScreenPurposeInferenceService
from src.analysis.prompts import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from src.analysis.schemas import GeneratedScreenPurposeCandidate, ScreenEvidencePackage
from src.analysis.validators import allowed_references, validate_capability_grounding
from src.database.enums import KnowledgeVersionStatus, SemanticType
from src.database.models import KnowledgeItem, KnowledgeVersionRecord, SemanticProposal
from src.database.repositories import SemanticProposalRepository
from src.database.services.semantic_exceptions import (
    SemanticCandidateMismatchError,
    SemanticEntityTypeError,
    SemanticIdentityCollisionError,
    SemanticScreenNotFoundError,
    SemanticScreenReviewError,
    SemanticVersionMismatchError,
    SemanticVersionNotActiveError,
)
from src.database.services.semantic_payloads import (
    ValidatedSemanticEvidenceSnapshot,
    canonical_json_hash,
    validated_semantic_evidence_snapshot,
)
from src.database.services.semantic_proposal_service import SemanticProposalService
from src.knowledge.canonical.enums import ReviewStatus


class PendingScreenPurposeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    knowledge_version_id: uuid.UUID
    screen_knowledge_item_id: uuid.UUID
    semantic_type: SemanticType
    source_payload: dict[str, Any]
    evidence_payload: ValidatedSemanticEvidenceSnapshot
    evidence_ids: list[str]
    generation_model: str
    prompt_version: str
    prompt_hash: str
    generation_parameters: dict[str, Any]


class ScreenPurposeProposalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: uuid.UUID
    semantic_id: str
    status: ReviewStatus
    semantic_type: SemanticType
    knowledge_version_id: uuid.UUID
    screen_knowledge_item_id: uuid.UUID
    screen_id: str
    evidence_hash: str
    generated_content_hash: str
    prompt_version: str
    prompt_hash: str
    generation_parameters_hash: str
    generation_model: str
    created: bool
    reused_existing: bool
    ollama_called: bool


def map_candidate_to_pending_proposal(
    *,
    package: ScreenEvidencePackage,
    candidate: GeneratedScreenPurposeCandidate,
    knowledge_version_id: uuid.UUID,
    screen_knowledge_item_id: uuid.UUID,
    expected_model: str,
) -> PendingScreenPurposeProposal:
    package_copy = ScreenEvidencePackage.model_validate(package.model_dump(mode="python"))
    candidate_copy = GeneratedScreenPurposeCandidate.model_validate(
        candidate.model_dump(mode="python")
    )
    inference_payload = candidate_copy.inference.model_dump(mode="json")
    checks = {
        "semantic_type": candidate_copy.inference.semantic_type == SemanticType.SCREEN_PURPOSE,
        "screen_id": candidate_copy.inference.screen_id == package_copy.screen_id,
        "evidence_hash": candidate_copy.evidence_hash == package_copy.evidence_hash,
        "evidence_ids": candidate_copy.evidence_ids == package_copy.evidence_ids,
        "generated_content_hash": candidate_copy.generated_content_hash
        == canonical_json_hash(inference_payload),
        "prompt_version": candidate_copy.prompt_version == PROMPT_VERSION,
        "prompt_hash": candidate_copy.prompt_hash == PROMPT_HASH,
        "generation_parameters": candidate_copy.generation_parameters == GENERATION_PARAMETERS,
        "generation_parameters_hash": candidate_copy.generation_parameters_hash
        == GENERATION_PARAMETERS_HASH,
        "generation_model": candidate_copy.generation_model == expected_model,
        "structured_output_mode": candidate_copy.structured_output_mode in {"json_schema", "json"},
        "references": all(
            reference in allowed_references(package_copy)
            for claim in candidate_copy.inference.supported_capabilities
            for reference in claim.evidence_refs
        ),
    }
    failed = sorted(name for name, valid in checks.items() if not valid)
    if failed:
        raise SemanticCandidateMismatchError(
            "El candidato semántico es incompatible: " + ", ".join(failed)
        )
    validate_capability_grounding(candidate_copy.inference, package_copy)
    evidence_snapshot = validated_semantic_evidence_snapshot(package_copy)
    if evidence_snapshot.evidence_hash != package_copy.evidence_hash:
        raise SemanticCandidateMismatchError("El snapshot de evidencia es incompatible")
    return PendingScreenPurposeProposal(
        knowledge_version_id=knowledge_version_id,
        screen_knowledge_item_id=screen_knowledge_item_id,
        semantic_type=SemanticType.SCREEN_PURPOSE,
        source_payload=inference_payload,
        evidence_payload=evidence_snapshot,
        evidence_ids=list(package_copy.evidence_ids),
        generation_model=candidate_copy.generation_model,
        prompt_version=candidate_copy.prompt_version,
        prompt_hash=candidate_copy.prompt_hash,
        generation_parameters=dict(candidate_copy.generation_parameters),
    )


class ScreenPurposeProposalWorkflow:
    def __init__(
        self,
        session: Session,
        *,
        evidence_builder: ScreenEvidenceBuilder | None = None,
        inference_service: ScreenPurposeInferenceService,
        proposal_service: SemanticProposalService | None = None,
    ):
        self.session = session
        self.evidence_builder = evidence_builder or ScreenEvidenceBuilder(session)
        self.inference_service = inference_service
        self.proposal_service = proposal_service or SemanticProposalService(session)
        self.proposals = SemanticProposalRepository(session)

    def generate_candidate(self, knowledge_version_id, screen_knowledge_item_id):
        package, _, _ = self._context(knowledge_version_id, screen_knowledge_item_id)
        return self.inference_service.generate(package)

    def persist_candidate(self, knowledge_version_id, screen_knowledge_item_id, candidate):
        package, version, screen = self._context(knowledge_version_id, screen_knowledge_item_id)
        mapped = map_candidate_to_pending_proposal(
            package=package,
            candidate=candidate,
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            expected_model=self.inference_service.client.settings.model,
        )
        existing = self._existing(mapped, package.evidence_hash)
        proposal = self.proposal_service.create_pending_proposal(**self._proposal_kwargs(mapped))
        return self._result(
            proposal,
            screen,
            created=existing is None,
            reused_existing=existing is not None,
            ollama_called=False,
        )

    def generate_and_persist(self, knowledge_version_id, screen_knowledge_item_id):
        package, version, screen = self._context(knowledge_version_id, screen_knowledge_item_id)
        existing = self._existing_for_active_configuration(package, version, screen)
        if existing is not None:
            self._verify_reusable(existing, package)
            return self._result(
                existing,
                screen,
                created=False,
                reused_existing=True,
                ollama_called=False,
            )
        candidate = self.inference_service.generate(package)
        mapped = map_candidate_to_pending_proposal(
            package=package,
            candidate=candidate,
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            expected_model=self.inference_service.client.settings.model,
        )
        proposal = self.proposal_service.create_pending_proposal(**self._proposal_kwargs(mapped))
        return self._result(
            proposal,
            screen,
            created=True,
            reused_existing=False,
            ollama_called=True,
        )

    def _context(self, version_id, screen_id):
        version = self.session.get(KnowledgeVersionRecord, self._uuid(version_id))
        if version is None:
            raise SemanticVersionMismatchError("La versión de conocimiento no existe")
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise SemanticVersionNotActiveError("La versión de conocimiento no está activa")
        screen = self.session.get(KnowledgeItem, self._uuid(screen_id))
        if screen is None:
            raise SemanticScreenNotFoundError("La pantalla no existe")
        if screen.entity_type != "screen":
            raise SemanticEntityTypeError("El KnowledgeItem no es una pantalla")
        if screen.knowledge_version_id != version.id:
            raise SemanticVersionMismatchError("La pantalla pertenece a otra versión")
        if screen.current_review_status not in {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}:
            raise SemanticScreenReviewError("La pantalla no tiene revisión publicable")
        package = self.evidence_builder.build(version.id, screen.id)
        context_checks = {
            "erp": package.erp_id == version.erp_id,
            "knowledge_version_id": package.knowledge_version_id == version.id,
            "knowledge_version": package.knowledge_version == version.knowledge_version,
            "screen": package.screen_id == screen.canonical_id,
            "screen_version": screen.knowledge_version_id == version.id,
        }
        failed = sorted(name for name, valid in context_checks.items() if not valid)
        if failed:
            raise SemanticVersionMismatchError(
                "El contexto de evidencia es incompatible: " + ", ".join(failed)
            )
        return package, version, screen

    def _existing_for_active_configuration(self, package, version, screen):
        return self.proposals.get_by_generation_identity(
            knowledge_version_id=version.id,
            screen_knowledge_item_id=screen.id,
            semantic_type=SemanticType.SCREEN_PURPOSE,
            evidence_hash=package.evidence_hash,
            prompt_hash=PROMPT_HASH,
            generation_model=self.inference_service.client.settings.model,
            generation_parameters_hash=GENERATION_PARAMETERS_HASH,
        )

    def _existing(self, mapped, evidence_hash):
        return self.proposals.get_by_generation_identity(
            knowledge_version_id=mapped.knowledge_version_id,
            screen_knowledge_item_id=mapped.screen_knowledge_item_id,
            semantic_type=mapped.semantic_type,
            evidence_hash=evidence_hash,
            prompt_hash=mapped.prompt_hash,
            generation_model=mapped.generation_model,
            generation_parameters_hash=GENERATION_PARAMETERS_HASH,
        )

    @staticmethod
    def _verify_reusable(proposal, package):
        expected_evidence = package.model_dump(mode="json", exclude={"evidence_hash"})
        incompatible = (
            proposal.evidence_payload != expected_evidence
            or proposal.evidence_ids != list(package.evidence_ids)
            or proposal.prompt_version != PROMPT_VERSION
            or proposal.generation_parameters != GENERATION_PARAMETERS
        )
        if incompatible:
            raise SemanticIdentityCollisionError(
                "La propuesta existente no es reutilizable con la configuración activa"
            )

    @staticmethod
    def _proposal_kwargs(mapped):
        return {
            "knowledge_version_id": mapped.knowledge_version_id,
            "screen_knowledge_item_id": mapped.screen_knowledge_item_id,
            "semantic_type": mapped.semantic_type,
            "source_payload": dict(mapped.source_payload),
            "evidence_payload": mapped.evidence_payload,
            "evidence_ids": list(mapped.evidence_ids),
            "generation_model": mapped.generation_model,
            "prompt_version": mapped.prompt_version,
            "prompt_hash": mapped.prompt_hash,
            "generation_parameters": dict(mapped.generation_parameters),
        }

    @staticmethod
    def _result(proposal: SemanticProposal, screen: KnowledgeItem, **flags):
        return ScreenPurposeProposalResult(
            proposal_id=proposal.id,
            semantic_id=proposal.semantic_id,
            status=proposal.current_review_status,
            semantic_type=proposal.semantic_type,
            knowledge_version_id=proposal.knowledge_version_id,
            screen_knowledge_item_id=proposal.screen_knowledge_item_id,
            screen_id=screen.canonical_id,
            evidence_hash=proposal.evidence_hash,
            generated_content_hash=proposal.source_content_hash,
            prompt_version=proposal.prompt_version,
            prompt_hash=proposal.prompt_hash,
            generation_parameters_hash=proposal.generation_parameters_hash,
            generation_model=proposal.generation_model,
            **flags,
        )

    @staticmethod
    def _uuid(value):
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise SemanticVersionMismatchError("Identificador inválido") from exc
