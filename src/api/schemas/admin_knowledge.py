from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from src.analysis.schemas import (
    ControlEvidence,
    EventEvidence,
    FieldEvidence,
    ModuleEvidence,
    ScreenPurposeInference,
    TableEvidence,
    TransitionEvidence,
    UIStateEvidence,
)
from src.api.schemas.semantic_review import StrictResponseModel
from src.knowledge.canonical.enums import ReviewStatus


class ScreenSemanticState(StrEnum):
    NO_PROPOSAL = "no_proposal"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


class KnowledgeCounters(StrictResponseModel):
    total_screens: int
    no_proposal: int
    pending_review: int
    approved: int
    corrected: int
    rejected: int
    unavailable: int
    warnings_total: int


class KnowledgeTreeScreen(StrictResponseModel):
    screen_id: str
    title: str | None
    route: str | None
    structural_review_status: ReviewStatus
    structural_available: bool = True
    diagnostic: str | None = None
    semantic_state: ScreenSemanticState
    proposal_count: int
    pending_count: int
    latest_semantic_id: str | None
    latest_semantic_status: ReviewStatus | None
    capabilities_count: int | None
    evidence_available: bool
    warnings_count: int


class KnowledgeTreeModule(StrictResponseModel):
    module_id: str
    name: str | None
    route: str | None
    available: bool = True
    diagnostic: str | None = None
    order: int
    screens: tuple[KnowledgeTreeScreen, ...]
    counters: KnowledgeCounters


class KnowledgeTreeErp(StrictResponseModel):
    erp_id: str
    name: str
    slug: str
    active_knowledge_version_id: str
    knowledge_version: str
    modules: tuple[KnowledgeTreeModule, ...]
    unassigned_screens: tuple[KnowledgeTreeScreen, ...]
    warnings: tuple[str, ...] = ()
    counters: KnowledgeCounters


class KnowledgeTreeResponse(StrictResponseModel):
    erps: tuple[KnowledgeTreeErp, ...]


class AdminErpSummary(StrictResponseModel):
    erp_id: str
    name: str
    slug: str


class AdminVersionSummary(StrictResponseModel):
    knowledge_version_id: str
    knowledge_version: str
    status: str


class AdminModuleSummary(StrictResponseModel):
    module_id: str
    name: str | None
    route: str | None


class AdminScreenDetail(StrictResponseModel):
    screen_id: str
    title: str | None
    route: str | None
    structural_review_status: ReviewStatus
    structural_available: bool
    diagnostic: str | None = None


class AdminEvidence(StrictResponseModel):
    evidence_available: bool
    diagnostic: str | None = None
    screen_id: str
    screen_title: str | None
    screen_route: str | None
    module: ModuleEvidence | None
    fields: tuple[FieldEvidence, ...] = ()
    controls: tuple[ControlEvidence, ...] = ()
    tables: tuple[TableEvidence, ...] = ()
    ui_states: tuple[UIStateEvidence, ...] = ()
    events: tuple[EventEvidence, ...] = ()
    transitions: tuple[TransitionEvidence, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    current_structure_hash: str


class ComparableScreenStructure(StrictResponseModel):
    screen_id: str
    screen_title: str | None
    screen_route: str | None
    module: ModuleEvidence | None
    fields: tuple[FieldEvidence, ...] = ()
    controls: tuple[ControlEvidence, ...] = ()
    tables: tuple[TableEvidence, ...] = ()
    ui_states: tuple[UIStateEvidence, ...] = ()
    events: tuple[EventEvidence, ...] = ()
    transitions: tuple[TransitionEvidence, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class AdminProposalSummary(StrictResponseModel):
    semantic_id: str
    semantic_type: str
    current_review_status: ReviewStatus
    review_revision: int
    erp_id: str
    knowledge_version_id: str
    screen_id: str
    subject_title: str | None
    purpose_summary: str | None
    generation_model: str
    prompt_version: str
    evidence_hash: str
    created_at: datetime
    updated_at: datetime
    review_action_count: int
    diagnostic: str | None = None


class HistoricalProposalEvidence(StrictResponseModel):
    evidence_available: bool
    diagnostic: str | None = None
    screen_id: str | None = None
    screen_title: str | None = None
    screen_route: str | None = None
    module: ModuleEvidence | None = None
    fields: tuple[FieldEvidence, ...] = ()
    controls: tuple[ControlEvidence, ...] = ()
    tables: tuple[TableEvidence, ...] = ()
    ui_states: tuple[UIStateEvidence, ...] = ()
    events: tuple[EventEvidence, ...] = ()
    transitions: tuple[TransitionEvidence, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_hash: str


class ProposalContext(StrictResponseModel):
    summary: AdminProposalSummary
    effective_payload: ScreenPurposeInference | None
    evidence: HistoricalProposalEvidence
    historical_structure_hash: str | None
    current_structure_hash: str
    evidence_matches_current_structure: bool
    diagnostic: str | None = None


class AdminReviewHistoryItem(StrictResponseModel):
    semantic_id: str
    action: str
    previous_status: ReviewStatus
    new_status: ReviewStatus
    reason: str | None
    reviewer_id: str
    reviewer_identity_verified: Literal[False] = False
    corrected_payload: ScreenPurposeInference | None
    created_at: datetime
    diagnostic: str | None = None


class TraceabilitySummary(StrictResponseModel):
    proposal_count: int
    review_action_count: int
    evidence_available: bool
    evidence_ids: tuple[str, ...]
    warnings: tuple[str, ...]


class ScreenNavigation(StrictResponseModel):
    previous_screen_id: str | None
    next_screen_id: str | None
    module_screen_position: int
    module_screen_total: int


class ScreenReviewContextResponse(StrictResponseModel):
    erp: AdminErpSummary
    version: AdminVersionSummary
    module: AdminModuleSummary | None
    screen: AdminScreenDetail
    structural_evidence: AdminEvidence
    semantic_proposals: tuple[ProposalContext, ...]
    active_proposal: ProposalContext | None
    review_history: tuple[AdminReviewHistoryItem, ...]
    effective_payload: ScreenPurposeInference | None
    traceability: TraceabilitySummary
    semantic_state: ScreenSemanticState
    navigation: ScreenNavigation
    reviewer_identity_verified: Literal[False] = False


class AdminScreenListItem(StrictResponseModel):
    erp_id: str
    knowledge_version_id: str
    module_id: str | None
    module_name: str | None
    screen: KnowledgeTreeScreen


class AdminScreenListResponse(StrictResponseModel):
    items: tuple[AdminScreenListItem, ...]
    total: int
    limit: int
    offset: int
    next_offset: int | None
