export type ReviewStatus = 'pending_review' | 'approved' | 'corrected' | 'rejected'
export type ScreenSemanticState = 'no_proposal' | ReviewStatus | 'mixed' | 'unavailable'

export interface AdminCounters { total_screens: number; no_proposal: number; pending_review: number; approved: number; corrected: number; rejected: number; unavailable: number; warnings_total: number }
export interface KnowledgeTreeScreen { screen_id: string; title: string | null; route: string | null; structural_review_status: ReviewStatus; structural_available: boolean; diagnostic: string | null; semantic_state: ScreenSemanticState; proposal_count: number; pending_count: number; latest_semantic_id: string | null; latest_semantic_status: ReviewStatus | null; capabilities_count: number | null; evidence_available: boolean; warnings_count: number }
export interface KnowledgeTreeModule { module_id: string; name: string | null; route: string | null; available: boolean; diagnostic: string | null; order: number; screens: KnowledgeTreeScreen[]; counters: AdminCounters }
export interface KnowledgeTreeErp { erp_id: string; name: string; slug: string; active_knowledge_version_id: string; knowledge_version: string; modules: KnowledgeTreeModule[]; unassigned_screens: KnowledgeTreeScreen[]; warnings: string[]; counters: AdminCounters }
export interface KnowledgeTreeResponse { erps: KnowledgeTreeErp[] }
export interface AdminScreenListResponse { items: { erp_id: string; knowledge_version_id: string; module_id: string | null; module_name: string | null; screen: KnowledgeTreeScreen }[]; total: number; limit: number; offset: number; next_offset: number | null }

export interface ModuleEvidence { module_id: string; name: string }
export interface FieldEvidence { field_id: string; label: string; input_type: string | null; required: boolean; readonly: boolean }
export interface ControlEvidence { control_id: string; label: string; control_type: string | null; mutative: boolean; safety_decision: string | null }
export interface ColumnEvidence { column_id: string; label: string }
export interface TableEvidence { table_id: string; name: string; columns: ColumnEvidence[] }
export interface UIStateEvidence { state_id: string; title: string; depth: number | null }
export interface EventEvidence { event_id: string; label: string; category: string; policy_decision: string; mutative: boolean }
export interface TransitionEvidence { transition_id: string; category: string; source_state_id: string | null; target_state_id: string | null; trigger_control_id: string | null }
export interface ComparableStructure { screen_id: string | null; screen_title: string | null; screen_route: string | null; module: ModuleEvidence | null; fields: FieldEvidence[]; controls: ControlEvidence[]; tables: TableEvidence[]; ui_states: UIStateEvidence[]; events: EventEvidence[]; transitions: TransitionEvidence[]; evidence_ids: string[] }
export interface StructuralEvidence extends ComparableStructure { evidence_available: boolean; diagnostic: string | null; screen_id: string; warnings: string[]; current_structure_hash: string }
export interface CapabilityClaim { statement: string; evidence_refs: string[] }
export interface ScreenPurposeInference { semantic_type: 'screen_purpose'; screen_id: string; purpose_summary: string; supported_capabilities: CapabilityClaim[]; limitations: string[]; uncertainties: string[] }
export interface AdminProposalSummary { semantic_id: string; semantic_type: string; current_review_status: ReviewStatus; review_revision: number; erp_id: string; knowledge_version_id: string; screen_id: string; subject_title: string | null; purpose_summary: string | null; generation_model: string; prompt_version: string; evidence_hash: string; created_at: string; updated_at: string; review_action_count: number; diagnostic: string | null }
export interface HistoricalProposalEvidence extends ComparableStructure { evidence_available: boolean; diagnostic: string | null; warnings: string[]; evidence_hash: string }
export interface ProposalContext { summary: AdminProposalSummary; effective_payload: ScreenPurposeInference | null; evidence: HistoricalProposalEvidence; historical_structure_hash: string | null; current_structure_hash: string; evidence_matches_current_structure: boolean; diagnostic: string | null }
export interface TraceabilitySummary { proposal_count: number; review_action_count: number; evidence_available: boolean; evidence_ids: string[]; warnings: string[] }
export interface ScreenNavigation { previous_screen_id: string | null; next_screen_id: string | null; module_screen_position: number; module_screen_total: number }
export interface ReviewHistoryItem { semantic_id: string; action: string; previous_status: ReviewStatus; new_status: ReviewStatus; reason: string | null; reviewer_id: string; reviewer_identity_verified: false; corrected_payload: ScreenPurposeInference | null; created_at: string; diagnostic: string | null }
export interface ScreenReviewContextResponse { erp: { erp_id: string; name: string; slug: string }; version: { knowledge_version_id: string; knowledge_version: string; status: string }; module: { module_id: string; name: string | null; route: string | null } | null; screen: { screen_id: string; title: string | null; route: string | null; structural_review_status: ReviewStatus; structural_available: boolean; diagnostic: string | null }; structural_evidence: StructuralEvidence; semantic_proposals: ProposalContext[]; active_proposal: ProposalContext | null; review_history: ReviewHistoryItem[]; effective_payload: ScreenPurposeInference | null; traceability: TraceabilitySummary; semantic_state: ScreenSemanticState; navigation: ScreenNavigation; reviewer_identity_verified: false }
