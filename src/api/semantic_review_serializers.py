from __future__ import annotations

from pydantic import ValidationError

from src.analysis.schemas import ScreenEvidencePackage, ScreenPurposeInference
from src.api.schemas.semantic_review import (
    GenerationTraceResponse,
    ReviewActionResponse,
    ScreenEvidenceReviewResponse,
    SemanticProposalDetailResponse,
    SemanticProposalSummaryResponse,
)
from src.database.models import SemanticProposal
from src.database.repositories import SemanticReviewActionRepository
from src.database.services import SemanticEffectivePayloadService
from src.database.services.semantic_exceptions import SemanticHistoryIntegrityError


def proposal_summary(
    proposal: SemanticProposal, action_count: int
) -> SemanticProposalSummaryResponse:
    source = ScreenPurposeInference.model_validate(proposal.source_payload)
    return SemanticProposalSummaryResponse(
        semantic_id=proposal.semantic_id,
        semantic_type=str(proposal.semantic_type),
        current_review_status=proposal.current_review_status,
        review_revision=proposal.review_revision,
        erp_id=proposal.knowledge_version.erp_id,
        knowledge_version_id=str(proposal.knowledge_version_id),
        screen_id=proposal.screen_knowledge_item.canonical_id,
        subject_title=proposal.screen_knowledge_item.title,
        purpose_summary=source.purpose_summary,
        generation_model=proposal.generation_model,
        prompt_version=proposal.prompt_version,
        evidence_hash=proposal.evidence_hash,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        review_action_count=action_count,
    )


def review_evidence(proposal: SemanticProposal) -> ScreenEvidenceReviewResponse:
    try:
        package = ScreenEvidencePackage.model_validate(
            {**proposal.evidence_payload, "evidence_hash": proposal.evidence_hash}
        )
    except (ValidationError, TypeError, ValueError):
        return ScreenEvidenceReviewResponse(
            evidence_available=False,
            diagnostic="La evidencia persistida no cumple el esquema esperado.",
            evidence_hash=proposal.evidence_hash,
        )
    return ScreenEvidenceReviewResponse(
        evidence_available=True,
        evidence_hash=proposal.evidence_hash,
        screen_id=package.screen_id,
        screen_title=package.screen_title,
        screen_route=package.screen_route,
        module=package.module.model_dump(mode="json"),
        fields=tuple(item.model_dump(mode="json") for item in package.fields),
        controls=tuple(item.model_dump(mode="json") for item in package.controls),
        tables=tuple(item.model_dump(mode="json") for item in package.tables),
        events=tuple(item.model_dump(mode="json") for item in package.events),
        transitions=tuple(item.model_dump(mode="json") for item in package.transitions),
        evidence_ids=tuple(package.evidence_ids),
        warnings=tuple(package.warnings),
    )


def proposal_detail(proposal: SemanticProposal, session) -> SemanticProposalDetailResponse:
    actions = SemanticReviewActionRepository(session).list_for_proposal(proposal.id)
    effective = SemanticEffectivePayloadService(session).effective_payload(proposal.id)
    history = tuple(
        ReviewActionResponse(
            action=str(action.action),
            previous_status=action.previous_status,
            new_status=action.new_status,
            reason=action.review_notes,
            reviewer_id=action.reviewer_subject,
            corrected_payload=(
                ScreenPurposeInference.model_validate(action.corrected_payload)
                if action.corrected_payload is not None
                else None
            ),
            created_at=action.created_at,
        )
        for action in actions
    )
    return SemanticProposalDetailResponse(
        summary=proposal_summary(proposal, len(actions)),
        source_payload=ScreenPurposeInference.model_validate(proposal.source_payload),
        evidence=review_evidence(proposal),
        evidence_ids=tuple(proposal.evidence_ids),
        generation_trace=GenerationTraceResponse(
            generation_model=proposal.generation_model,
            prompt_version=proposal.prompt_version,
            prompt_hash=proposal.prompt_hash,
            generation_parameters=dict(proposal.generation_parameters),
            generation_parameters_hash=proposal.generation_parameters_hash,
            source_content_hash=proposal.source_content_hash,
        ),
        review_history=history,
        effective_payload=ScreenPurposeInference.model_validate(effective),
        publishable=SemanticEffectivePayloadService(session).publishable_payload(proposal.id)
        is not None,
    )


def validated_evidence_package(proposal: SemanticProposal) -> ScreenEvidencePackage:
    try:
        return ScreenEvidencePackage.model_validate(
            {**proposal.evidence_payload, "evidence_hash": proposal.evidence_hash}
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise SemanticHistoryIntegrityError(
            "La evidencia persistida no cumple el esquema esperado"
        ) from exc
