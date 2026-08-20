from __future__ import annotations

from src.analysis.evidence.screen_evidence_builder import (
    ScreenEvidenceBuilder,
    ScreenEvidenceError,
)
from src.analysis.eligibility import evaluate_screen_semantic_eligibility
from src.database.repositories import SemanticProposalRepository
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import sanitize_text

from .semantic_effective_payload_service import SemanticEffectivePayloadService

PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


class SemanticRetrievalAuthorizationService:
    """Re-authorize semantic Chroma candidates against PostgreSQL.

    Chroma is only a relevance projection. A candidate is usable at query time
    only when the semantic proposal, its structural screen, its review revision,
    and its evidence snapshot still match PostgreSQL and the current active
    structural evidence.
    """

    def __init__(
        self,
        session,
        *,
        proposals=None,
        effective=None,
        evidence_builder=None,
    ):
        self.session = session
        self.proposals = proposals or SemanticProposalRepository(session)
        self.effective = effective or SemanticEffectivePayloadService(session)
        self.evidence_builder = evidence_builder or ScreenEvidenceBuilder(session)

    def authorize_hits(self, hits, *, version):
        authorized = []
        seen = set()
        for hit in hits or []:
            semantic_id = str(hit.get("semantic_id") or "").strip()
            if not semantic_id or semantic_id in seen:
                continue
            seen.add(semantic_id)
            proposal = self.proposals.get_by_semantic_id(semantic_id)
            if proposal is None or proposal.knowledge_version_id != version.id:
                continue
            if proposal.current_review_status not in PUBLISHABLE:
                continue
            screen = proposal.screen_knowledge_item
            if (
                screen is None
                or screen.knowledge_version_id != version.id
                or screen.current_review_status not in PUBLISHABLE
            ):
                continue
            if not self._projection_matches(hit, proposal, screen):
                continue
            try:
                package = self.evidence_builder.build(version.id, screen.id)
            except ScreenEvidenceError:
                continue
            if not evaluate_screen_semantic_eligibility(package).eligible:
                continue
            if (
                proposal.evidence_hash != package.evidence_hash
                or list(proposal.evidence_ids) != list(package.evidence_ids)
            ):
                continue
            payload = self.effective.publishable_payload(proposal.id)
            if not isinstance(payload, dict):
                continue
            purpose = self._safe(payload.get("purpose_summary"), 1000)
            title = self._safe(package.screen_title, 240)
            route = self._safe(package.screen_route, 500)
            if not purpose or not title:
                continue
            capabilities = []
            for capability in payload.get("supported_capabilities") or []:
                if not isinstance(capability, dict):
                    continue
                statement = self._safe(capability.get("statement"), 1000)
                if statement:
                    capabilities.append(statement)
            authorized.append(
                {
                    "semantic_id": proposal.semantic_id,
                    "semantic_type": str(proposal.semantic_type),
                    "canonical_id": screen.canonical_id,
                    "screen_id": screen.canonical_id,
                    "screen_route": route,
                    "safe_label": title,
                    "review_status": str(proposal.current_review_status),
                    "review_revision": int(proposal.review_revision),
                    "evidence_hash": proposal.evidence_hash,
                    "evidence_ids": list(proposal.evidence_ids),
                    "purpose_summary": purpose,
                    "supported_capabilities": capabilities,
                    "score": hit.get("score"),
                    "distance": hit.get("distance"),
                }
            )
        return authorized

    @staticmethod
    def _projection_matches(hit, proposal, screen):
        try:
            hit_revision = int(hit.get("review_revision"))
        except (TypeError, ValueError):
            return False
        return all(
            (
                hit.get("semantic_type") == str(proposal.semantic_type),
                hit.get("screen_id") == screen.canonical_id,
                hit.get("canonical_id") == screen.canonical_id,
                hit.get("review_status") == str(proposal.current_review_status),
                hit_revision == int(proposal.review_revision),
                hit.get("evidence_hash") == proposal.evidence_hash,
            )
        )

    @staticmethod
    def _safe(value, limit):
        clean, detections = sanitize_text(value, limit)
        return clean if clean and not detections else ""
