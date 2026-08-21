export type ReviewStatus = 'pending_review' | 'approved' | 'corrected' | 'rejected'
export type ScreenSemanticState = 'no_proposal' | ReviewStatus | 'mixed' | 'unavailable'
export type SemanticLifecycleOrigin = 'generated' | 'carried_forward' | 'reinferred'

export interface AdminCounters { total_screens: number; no_proposal: number; pending_review: number; approved: number; corrected: number; rejected: number; unavailable: number; warnings_total: number }
export interface KnowledgeTreeScreen { screen_id: string; title: string | null; route: string | null; structural_review_status: ReviewStatus; structural_available: boolean; diagnostic: string | null; semantic_state: ScreenSemanticState; proposal_count: number; pending_count: number; latest_semantic_id: string | null; latest_semantic_status: ReviewStatus | null; capabilities_count: number | null; evidence_available: boolean; warnings_count: number }
export interface KnowledgeTreeModule { module_id: string; parent_module_id: string | null; depth: number; navigation_path: string[]; name: string | null; route: string | null; available: boolean; diagnostic: string | null; order: number; screens: KnowledgeTreeScreen[]; counters: AdminCounters }
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
export interface NetworkTraceEvidence { evidence_id: string; methods: string[]; endpoint_paths: string[]; resource_types: string[]; origin_kinds: string[]; status_codes: number[]; query_keys: string[]; observation_count: number; endpoint_count: number; read_only: boolean }
export interface ComparableStructure { screen_id: string | null; screen_title: string | null; screen_route: string | null; module: ModuleEvidence | null; fields: FieldEvidence[]; controls: ControlEvidence[]; tables: TableEvidence[]; ui_states: UIStateEvidence[]; events: EventEvidence[]; transitions: TransitionEvidence[]; network_traces: NetworkTraceEvidence[]; evidence_ids: string[] }
export interface StructuralEvidence extends ComparableStructure { evidence_available: boolean; diagnostic: string | null; screen_id: string; warnings: string[]; current_structure_hash: string }
export interface CapabilityClaim { statement: string; evidence_refs: string[] }
export interface ScreenPurposeInference { semantic_type: 'screen_purpose'; screen_id: string; purpose_summary: string; supported_capabilities: CapabilityClaim[]; limitations: string[]; uncertainties: string[] }
export interface AdminProposalSummary { semantic_id: string; semantic_type: string; current_review_status: ReviewStatus; review_revision: number; lifecycle_origin: SemanticLifecycleOrigin; source_semantic_proposal_id: string | null; source_knowledge_version_id: string | null; source_review_status: ReviewStatus | null; source_review_revision: number | null; source_effective_content_hash: string | null; erp_id: string; knowledge_version_id: string; screen_id: string; subject_title: string | null; purpose_summary: string | null; generation_model: string; prompt_version: string; evidence_hash: string; created_at: string; updated_at: string; review_action_count: number; diagnostic: string | null }
export interface HistoricalProposalEvidence extends ComparableStructure { evidence_available: boolean; diagnostic: string | null; warnings: string[]; evidence_hash: string }
export interface ProposalContext { summary: AdminProposalSummary; effective_payload: ScreenPurposeInference | null; evidence: HistoricalProposalEvidence; historical_structure_hash: string | null; current_structure_hash: string; evidence_matches_current_structure: boolean; diagnostic: string | null }
export interface TraceabilitySummary { proposal_count: number; review_action_count: number; evidence_available: boolean; evidence_ids: string[]; warnings: string[] }
export interface ScreenNavigation { previous_screen_id: string | null; next_screen_id: string | null; module_screen_position: number; module_screen_total: number }
export interface ReviewHistoryItem { semantic_id: string; action: string; previous_status: ReviewStatus; new_status: ReviewStatus; reason: string | null; reviewer_id: string; reviewer_identity_verified: false; corrected_payload: ScreenPurposeInference | null; created_at: string; diagnostic: string | null }
export interface ScreenReviewContextResponse { erp: { erp_id: string; name: string; slug: string }; version: { knowledge_version_id: string; knowledge_version: string; status: string }; module: { module_id: string; name: string | null; route: string | null } | null; screen: { screen_id: string; title: string | null; route: string | null; structural_review_status: ReviewStatus; structural_available: boolean; diagnostic: string | null }; structural_evidence: StructuralEvidence; semantic_proposals: ProposalContext[]; active_proposal: ProposalContext | null; review_history: ReviewHistoryItem[]; effective_payload: ScreenPurposeInference | null; traceability: TraceabilitySummary; semantic_state: ScreenSemanticState; navigation: ScreenNavigation; reviewer_identity_verified: false }

export interface AdminSystemStatusResponse {
  ok: boolean
  generated_at: string
  services: {
    postgresql: { status: string; active_version?: string | null; detail?: string }
    neo4j: { status: string; uri?: string; database?: string; server_agent?: string; nodes?: number; relationships?: number; versions?: string[]; constraints?: number; detail?: string }
    chroma: { status: string; collection?: string; documents?: number; detail?: string }
    semantic_chroma?: { status: string; collection?: string; documents?: number; detail?: string }
    ollama: { status: string; configured_embedding_model?: string; configured_embedding_model_available?: boolean; models?: string[]; detail?: string }
  }
  knowledge: {
    active_version: string | null
    total_items: number
    approved: number
    corrected: number
    pending_review: number
    rejected: number
    items_by_status: Record<string, number>
    latest_import: { id: string; status: string; requested_knowledge_version: string; inserted_items: number; started_at: string | null; finished_at: string | null } | null
    sync_jobs: Array<{ id: string; target: string; status: string; attempt_count: number; requested_at: string | null; started_at: string | null; finished_at: string | null; error_summary: string | null; checkpoint: Record<string, unknown> }>
  }
}


export type PipelineJobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export type PipelineJobScope = 'full' | 'module' | 'screen' | 'version' | 'system'

export interface PipelineJobSummary {
  id: string
  kind: string
  status: PipelineJobStatus
  scope: PipelineJobScope
  target: string | null
  profile_name: string | null
  erp_id: string | null
  knowledge_version_id: string | null
  request_source: string
  stage: string
  progress_current: number
  progress_total: number | null
  progress_percent: number | null
  requested_at: string
  started_at: string | null
  finished_at: string | null
  error_summary: string | null
}

export interface PipelineJobDetail extends PipelineJobSummary {
  parameters: Record<string, unknown>
  checkpoint: Record<string, unknown>
  result_payload: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface PipelineJobListResponse {
  items: PipelineJobSummary[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export interface CrawlJobRequest {
  scope: PipelineJobScope
  target?: string | null
  target_module_id?: string | null
  knowledge_version_id?: string | null
  headless: boolean
  slow_mo: number
}

export interface CanonicalBuildJobRequest {
  source_crawl_job_id: string
}

export interface CanonicalMergeJobRequest {
  source_canonical_job_id: string
}

export interface CanonicalReconciliationJobRequest {
  candidate_version_id: string
}

export interface CanonicalImportJobRequest {
  source_canonical_job_id?: string
  source_reconciliation_job_id?: string
}


export interface SemanticInferenceJobRequest {
  screen_id: string
}

export interface SemanticReviewRequest {
  reviewer_id: string
  reason: string
  expected_status: ReviewStatus
  expected_revision: number
}

export interface SemanticCorrectionRequest extends SemanticReviewRequest {
  corrected_payload: ScreenPurposeInference
}

export interface SemanticReviewResult {
  action: 'approve' | 'correct' | 'reject'
  semantic_id: string
  current_review_status: ReviewStatus
  review_revision: number
  effective_payload: ScreenPurposeInference
  publishable_payload: ScreenPurposeInference | null
  reviewer_identity_verified: false
}

export interface StructuralReviewItemSummary {
  id: string
  canonical_id: string
  entity_type: string
  parent_canonical_id: string | null
  title: string | null
  route: string | null
  current_review_status: ReviewStatus
  generated_review_status: ReviewStatus
  review_revision: number
  knowledge_version_id: string
  knowledge_version: string
  version_status: string
  content_hash: string
  created_at: string
  updated_at: string
}

export interface StructuralReviewAction {
  action: string
  previous_status: ReviewStatus
  new_status: ReviewStatus
  source: string
  reviewer_id: string | null
  reason: string | null
  corrected_payload: Record<string, unknown> | null
  created_at: string
}

export interface StructuralReviewItemDetail extends StructuralReviewItemSummary {
  source_payload: Record<string, unknown>
  corrected_payload: Record<string, unknown> | null
  effective_payload: Record<string, unknown>
  was_corrected: boolean
  review_history: StructuralReviewAction[]
  reviewer_identity_verified: false
}

export interface StructuralReviewListResponse {
  items: StructuralReviewItemSummary[]
  status_counts: Record<string, number>
  total: number
  limit: number
  offset: number
  next_offset: number | null
}


export interface StructuralReviewChange {
  change_type: 'unchanged' | 'new' | 'modified' | 'removed'
  entity_type: string
  canonical_id: string
  active_item_id: string | null
  candidate_item_id: string | null
  removal_confirmation: string | null
  requires_removal_review: boolean
}

export interface StructuralScreenReviewPackage {
  screen_id: string
  active_item_id: string | null
  candidate_item_id: string | null
  title: string | null
  route: string | null
  module_id: string | null
  module_path: string[]
  change_type: string
  active_review_status: string | null
  candidate_review_status: string | null
  carry_forward: boolean | null
  counts: Record<string, number>
  unconfirmed_removals: number
  review_required: boolean
  changes: StructuralReviewChange[]
}

export interface StructuralReviewPackagesResponse {
  active_version_id: string
  active_knowledge_version: string
  candidate_version_id: string
  candidate_knowledge_version: string
  erp_id: string
  candidate_origin: string
  diff_totals: Record<string, number>
  affected_screens: number
  screens_with_changes: number
  screens_unchanged: number
  unconfirmed_removals: number
  unscoped_changes: StructuralReviewChange[]
  packages: StructuralScreenReviewPackage[]
  total: number
  limit: number
  offset: number
  next_offset: number | null
}

export type StructuralPublicationScope = 'screen' | 'module' | 'system' | 'unscoped'

export interface StructuralPublicationReviewItem {
  item_id: string
  entity_type: string
  canonical_id: string
  title: string | null
  route: string | null
  review_status: ReviewStatus
  review_revision: number
  content_hash: string
}

export interface StructuralPublicationReviewPackage {
  scope_type: StructuralPublicationScope
  scope_id: string
  title: string | null
  route: string | null
  module_id: string | null
  module_path: string[]
  status_counts: Record<string, number>
  entity_counts: Record<string, number>
  pending_count: number
  publishable_count: number
  rejected_count: number
  review_required: boolean
  package_hash: string
  review_items: StructuralPublicationReviewItem[]
}

export interface StructuralPublicationReviewSummary {
  knowledge_version_id: string
  knowledge_version: string
  erp_id: string
  version_status: string
  status_counts: Record<string, number>
  publishable_count: number
  pending_count: number
  rejected_count: number
  package_count: number
  packages: StructuralPublicationReviewPackage[]
  total: number
  limit: number | null
  offset: number
  next_offset: number | null
}

export interface StructuralPublicationApproveRequest {
  scope_type: StructuralPublicationScope
  scope_id: string
  expected_package_hash: string
  reviewer_id: string
  reason: string
}

export interface StructuralPublicationApprovalResult {
  approved_count: number
  package: StructuralPublicationReviewPackage
}

export interface StructuralReviewRequest {
  reviewer_id: string
  reason?: string | null
  expected_status: ReviewStatus
  expected_revision: number
}

export interface StructuralCorrectionRequest extends StructuralReviewRequest {
  reason: string
  corrected_payload: Record<string, unknown>
}

export interface StructuralReviewResult extends StructuralReviewItemDetail {
  performed_action: 'approve' | 'correct' | 'reject' | 'reset_to_pending'
}


export type RemovalDecision = 'retain_from_active' | 'confirmed_remove'

export interface RemovalReviewDecision {
  id: string
  entity_type: string
  canonical_id: string
  active_item_id: string
  candidate_item_id: string | null
  screen_id: string | null
  plan_reason: string
  removal_confirmation: string | null
  proposed_decision: string
  current_decision: RemovalDecision | null
  requires_human_review: boolean
  review_revision: number
  decision_fingerprint: string
}

export interface RemovalReviewSet {
  id: string
  candidate_version_id: string
  candidate_knowledge_version: string
  active_version_id: string
  active_knowledge_version: string
  erp_id: string
  candidate_origin: string
  raw_diff_totals: Record<string, number>
  plan_hash: string
  decision_count: number
  pending_review: number
  retain_from_active: number
  confirmed_remove: number
  decisions: RemovalReviewDecision[]
}

export interface RemovalReviewRequest {
  reviewer_id: string
  reason: string
  expected_revision: number
}

export interface RemovalReviewResult extends RemovalReviewDecision {
  performed_action: 'confirm_retain' | 'confirm_remove' | 'reset_to_pending'
}

export interface RemovalReviewAction {
  id: string
  action: string
  previous_decision: RemovalDecision | null
  new_decision: RemovalDecision | null
  review_notes: string
  reviewer_subject: string
  source: string
  decision_fingerprint: string
  created_at: string
}

export interface RemovalReviewHistory {
  decision_id: string
  actions: RemovalReviewAction[]
}

export interface PromotionBlocker {
  code: string
  message: string
  count: number
  entity_type: string | null
}

export interface PromotionAssessment {
  knowledge_version_id: string
  knowledge_version: string
  erp_id: string
  version_status: string
  promotable: boolean
  bootstrap_promotion: boolean
  promotion_mode: string
  current_active_version_id: string | null
  current_active_knowledge_version: string | null
  required_entity_types: string[]
  required_review_counts: Record<string, Record<string, number>>
  all_review_counts: Record<string, number>
  replacement_review_counts: Record<string, number>
  diff_totals: Record<string, number> | null
  pipeline_import_job_id: string | null
  source_canonical_job_id: string | null
  source_reconciliation_job_id: string | null
  removal_review_set_id: string | null
  decision_set_hash: string | null
  build_warning_count: number
  blockers: PromotionBlocker[]
  warnings: string[]
}

export interface KnowledgeVersionPromoteRequest {
  reviewer_id: string
  reason: string
  expected_knowledge_version: string
  confirm_promotion: true
}

export interface KnowledgeVersionPromotionResult {
  promotion_id: string
  knowledge_version_id: string
  knowledge_version: string
  erp_id: string
  previous_active_version_id: string | null
  sync_jobs: Record<string, string>
  assessment: PromotionAssessment
}
