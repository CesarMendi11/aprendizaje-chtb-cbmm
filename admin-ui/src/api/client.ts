import { demoContexts, demoTree } from '../data/demoSnapshot'
import type { AdminSystemStatusResponse, CanonicalBuildJobRequest, CanonicalImportJobRequest, CanonicalMergeJobRequest, CanonicalReconciliationJobRequest, CrawlJobRequest, KnowledgeTreeResponse, KnowledgeVersionPromoteRequest, KnowledgeVersionPromotionResult, PipelineJobDetail, PipelineJobListResponse, PipelineJobSummary, PromotionAssessment, RemovalReviewHistory, RemovalReviewRequest, RemovalReviewResult, RemovalReviewSet, ScreenReviewContextResponse, SemanticCorrectionRequest, SemanticInferenceJobRequest, SemanticReviewRequest, SemanticReviewResult, StructuralCorrectionRequest, StructuralReviewItemDetail, StructuralReviewListResponse, StructuralReviewPackagesResponse, StructuralReviewRequest, StructuralReviewResult } from '../types/admin'

export type DataMode = 'demo' | 'live'
export const dataMode: DataMode = import.meta.env.VITE_ADMIN_API_MODE === 'live' ? 'live' : 'demo'

export class AdminApiError extends Error {
  constructor(public readonly kind: 'timeout' | 'network' | 'http' | 'invalid_response' | 'not_found', message: string, public readonly status?: number) { super(message); this.name = 'AdminApiError' }
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const hasString = (value: Record<string, unknown>, key: string) => typeof value[key] === 'string'
const validTree = (value: unknown): value is KnowledgeTreeResponse => isRecord(value) && Array.isArray(value.erps) && value.erps.every((erp) => isRecord(erp) && hasString(erp, 'erp_id') && hasString(erp, 'name') && Array.isArray(erp.modules) && Array.isArray(erp.unassigned_screens))
const validContext = (value: unknown): value is ScreenReviewContextResponse => isRecord(value) && isRecord(value.erp) && isRecord(value.screen) && hasString(value.erp, 'erp_id') && hasString(value.screen, 'screen_id') && isRecord(value.structural_evidence) && Array.isArray(value.semantic_proposals) && Array.isArray(value.review_history) && isRecord(value.traceability) && isRecord(value.navigation) && value.reviewer_identity_verified === false
const validSystemStatus = (value: unknown): value is AdminSystemStatusResponse => {
  if (!isRecord(value) || typeof value.ok !== 'boolean' || !hasString(value, 'generated_at') || !isRecord(value.services) || !isRecord(value.knowledge)) return false
  const services = value.services
  const knowledge = value.knowledge
  const validServices = ['postgresql', 'neo4j', 'chroma', 'ollama'].every((name) => isRecord(services[name]) && hasString(services[name] as Record<string, unknown>, 'status'))
  return validServices && typeof knowledge.total_items === 'number' && typeof knowledge.approved === 'number' && typeof knowledge.corrected === 'number' && typeof knowledge.pending_review === 'number' && typeof knowledge.rejected === 'number' && Array.isArray(knowledge.sync_jobs)
}



const reviewStatuses = new Set(['pending_review', 'approved', 'corrected', 'rejected'])
const validStructuralReviewItemSummary = (value: unknown): boolean => {
  if (!isRecord(value)) return false
  return hasString(value, 'id') && hasString(value, 'canonical_id') && hasString(value, 'entity_type') &&
    hasString(value, 'current_review_status') && reviewStatuses.has(String(value.current_review_status)) &&
    hasString(value, 'generated_review_status') && reviewStatuses.has(String(value.generated_review_status)) &&
    typeof value.review_revision === 'number' && hasString(value, 'knowledge_version_id') &&
    hasString(value, 'knowledge_version') && hasString(value, 'version_status') && hasString(value, 'content_hash') &&
    hasString(value, 'created_at') && hasString(value, 'updated_at')
}
const validStructuralReviewList = (value: unknown): value is StructuralReviewListResponse =>
  isRecord(value) && Array.isArray(value.items) && value.items.every(validStructuralReviewItemSummary) &&
  isRecord(value.status_counts) && typeof value.total === 'number' && typeof value.limit === 'number' &&
  typeof value.offset === 'number'
const validStructuralReviewDetail = (value: unknown): value is StructuralReviewItemDetail =>
  validStructuralReviewItemSummary(value) && isRecord(value) && isRecord(value.source_payload) &&
  isRecord(value.effective_payload) && Array.isArray(value.review_history) && value.reviewer_identity_verified === false
const validStructuralReviewResult = (value: unknown): value is StructuralReviewResult =>
  validStructuralReviewDetail(value) && isRecord(value) && hasString(value, 'performed_action')

const changeTypes = new Set(['unchanged', 'new', 'modified', 'removed'])
const validStructuralReviewChange = (value: unknown): boolean =>
  isRecord(value) && hasString(value, 'change_type') && changeTypes.has(String(value.change_type)) &&
  hasString(value, 'entity_type') && hasString(value, 'canonical_id') &&
  typeof value.requires_removal_review === 'boolean'
const validStructuralReviewPackage = (value: unknown): boolean =>
  isRecord(value) && hasString(value, 'screen_id') && isRecord(value.counts) &&
  typeof value.unconfirmed_removals === 'number' && typeof value.review_required === 'boolean' &&
  Array.isArray(value.module_path) && value.module_path.every((item) => typeof item === 'string') &&
  Array.isArray(value.changes) && value.changes.every(validStructuralReviewChange)
const validStructuralReviewPackages = (value: unknown): value is StructuralReviewPackagesResponse =>
  isRecord(value) && hasString(value, 'active_version_id') && hasString(value, 'active_knowledge_version') &&
  hasString(value, 'candidate_version_id') && hasString(value, 'candidate_knowledge_version') &&
  hasString(value, 'erp_id') && hasString(value, 'candidate_origin') && isRecord(value.diff_totals) &&
  typeof value.affected_screens === 'number' && typeof value.screens_with_changes === 'number' &&
  typeof value.screens_unchanged === 'number' && typeof value.unconfirmed_removals === 'number' &&
  Array.isArray(value.unscoped_changes) && value.unscoped_changes.every(validStructuralReviewChange) &&
  Array.isArray(value.packages) && value.packages.every(validStructuralReviewPackage) &&
  typeof value.total === 'number' && typeof value.limit === 'number' && typeof value.offset === 'number'

const validRemovalDecision = (value: unknown): boolean =>
  isRecord(value) && hasString(value, 'id') && hasString(value, 'entity_type') &&
  hasString(value, 'canonical_id') && hasString(value, 'active_item_id') &&
  hasString(value, 'plan_reason') && hasString(value, 'proposed_decision') &&
  typeof value.requires_human_review === 'boolean' && typeof value.review_revision === 'number' &&
  hasString(value, 'decision_fingerprint')

const validRemovalReviewSet = (value: unknown): value is RemovalReviewSet =>
  isRecord(value) && hasString(value, 'id') && hasString(value, 'candidate_version_id') &&
  hasString(value, 'candidate_knowledge_version') && hasString(value, 'active_version_id') &&
  hasString(value, 'active_knowledge_version') && hasString(value, 'erp_id') &&
  hasString(value, 'candidate_origin') && isRecord(value.raw_diff_totals) &&
  typeof value.decision_count === 'number' && typeof value.pending_review === 'number' &&
  typeof value.retain_from_active === 'number' && typeof value.confirmed_remove === 'number' &&
  Array.isArray(value.decisions) && value.decisions.every(validRemovalDecision)

const validRemovalReviewResult = (value: unknown): value is RemovalReviewResult =>
  validRemovalDecision(value) && isRecord(value) && hasString(value, 'performed_action')

const validRemovalReviewHistory = (value: unknown): value is RemovalReviewHistory =>
  isRecord(value) && hasString(value, 'decision_id') && Array.isArray(value.actions) &&
  value.actions.every((item) => isRecord(item) && hasString(item, 'id') &&
    hasString(item, 'action') && hasString(item, 'review_notes') &&
    hasString(item, 'reviewer_subject') && hasString(item, 'created_at'))

const validPromotionAssessment = (value: unknown): value is PromotionAssessment =>
  isRecord(value) && hasString(value, 'knowledge_version_id') &&
  hasString(value, 'knowledge_version') && hasString(value, 'erp_id') &&
  hasString(value, 'version_status') && typeof value.promotable === 'boolean' &&
  typeof value.bootstrap_promotion === 'boolean' && hasString(value, 'promotion_mode') &&
  Array.isArray(value.required_entity_types) && isRecord(value.required_review_counts) &&
  isRecord(value.all_review_counts) && isRecord(value.replacement_review_counts) &&
  Array.isArray(value.blockers) && Array.isArray(value.warnings)

const validPromotionResult = (value: unknown): value is KnowledgeVersionPromotionResult =>
  isRecord(value) && hasString(value, 'promotion_id') && hasString(value, 'knowledge_version_id') &&
  hasString(value, 'knowledge_version') && hasString(value, 'erp_id') &&
  isRecord(value.sync_jobs) && validPromotionAssessment(value.assessment)

const pipelineStatuses = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled'])
const pipelineScopes = new Set(['full', 'module', 'screen', 'version', 'system'])
const validPipelineJobSummary = (value: unknown): value is PipelineJobSummary => {
  if (!isRecord(value)) return false
  return hasString(value, 'id') && hasString(value, 'kind') && hasString(value, 'status') && pipelineStatuses.has(String(value.status)) && hasString(value, 'scope') && pipelineScopes.has(String(value.scope)) && hasString(value, 'request_source') && hasString(value, 'stage') && typeof value.progress_current === 'number' && hasString(value, 'requested_at')
}
const validPipelineJobDetail = (value: unknown): value is PipelineJobDetail => validPipelineJobSummary(value) && isRecord(value) && isRecord(value.parameters) && isRecord(value.checkpoint) && hasString(value, 'created_at') && hasString(value, 'updated_at')
const validPipelineJobList = (value: unknown): value is PipelineJobListResponse => isRecord(value) && Array.isArray(value.items) && value.items.every(validPipelineJobSummary) && typeof value.total === 'number' && typeof value.limit === 'number' && typeof value.offset === 'number'

const validScreenPurposeInference = (value: unknown): boolean =>
  isRecord(value) && value.semantic_type === 'screen_purpose' && hasString(value, 'screen_id') &&
  hasString(value, 'purpose_summary') && Array.isArray(value.supported_capabilities) &&
  Array.isArray(value.limitations) && Array.isArray(value.uncertainties)
const validSemanticReviewResult = (value: unknown): value is SemanticReviewResult =>
  isRecord(value) && ['approve', 'correct', 'reject'].includes(String(value.action)) &&
  hasString(value, 'semantic_id') && hasString(value, 'current_review_status') &&
  reviewStatuses.has(String(value.current_review_status)) && typeof value.review_revision === 'number' &&
  validScreenPurposeInference(value.effective_payload) &&
  (value.publishable_payload === null || validScreenPurposeInference(value.publishable_payload)) &&
  value.reviewer_identity_verified === false

async function request<T>(path: string, validate: (value: unknown) => value is T, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 8_000)
  try {
    const headers = new Headers(init.headers)
    headers.set('Accept', 'application/json')
    const response = await fetch(path, { ...init, signal: controller.signal, headers })
    if (!response.ok) throw new AdminApiError(response.status === 404 ? 'not_found' : 'http', response.status === 404 ? 'Pantalla no encontrada.' : `La API respondió con estado ${response.status}.`, response.status)
    const type = response.headers.get('content-type') ?? ''
    if (!type.includes('application/json')) throw new AdminApiError('invalid_response', 'La API no devolvió una respuesta JSON válida.')
    const value: unknown = await response.json()
    if (!validate(value)) throw new AdminApiError('invalid_response', 'La respuesta de la API no cumple el contrato administrativo esperado.')
    return value
  } catch (error: unknown) {
    if (error instanceof AdminApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') throw new AdminApiError('timeout', 'La API tardó demasiado en responder.')
    throw new AdminApiError('network', 'No fue posible conectar con la API administrativa.')
  } finally { window.clearTimeout(timeout) }
}

export async function getKnowledgeTree(options: { includeEmptyModules?: boolean } = {}): Promise<KnowledgeTreeResponse> {
  if (dataMode === 'demo') return Promise.resolve(demoTree)
  const query = new URLSearchParams()
  if (options.includeEmptyModules) query.set('include_empty_modules', 'true')
  const serialized = query.toString()
  const suffix = serialized ? `?${serialized}` : ''
  return request(`/api/admin/knowledge-tree${suffix}`, validTree)
}

export async function getScreenReviewContext(screenId: string): Promise<ScreenReviewContextResponse> {
  if (dataMode === 'demo') {
    const context = demoContexts[screenId]
    if (!context) throw new AdminApiError('not_found', 'Pantalla no encontrada en el snapshot de demostración.', 404)
    return Promise.resolve(context)
  }
  return request(`/api/admin/screens/${encodeURIComponent(screenId)}/review-context`, validContext)
}

export async function getSystemStatus(): Promise<AdminSystemStatusResponse> {
  return request('/api/admin/system/status', validSystemStatus)
}


export async function getPipelineJobs(limit = 12, kind?: string): Promise<PipelineJobListResponse> {
  if (dataMode !== 'live') return { items: [], total: 0, limit, offset: 0, next_offset: null }
  const query = new URLSearchParams({ limit: String(limit) })
  if (kind) query.set('kind', kind)
  return request(`/api/admin/pipeline-jobs?${query.toString()}`, validPipelineJobList)
}

export async function getPipelineJob(jobId: string): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('not_found', 'Los jobs sólo están disponibles en modo live.', 404)
  return request(`/api/admin/pipeline-jobs/${encodeURIComponent(jobId)}`, validPipelineJobDetail)
}

export async function createCrawlJob(payload: CrawlJobRequest): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'El crawler sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/crawl', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function createCanonicalBuildJob(payload: CanonicalBuildJobRequest): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'El Canonical Builder sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/canonical-build', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function createCanonicalMergeJob(payload: CanonicalMergeJobRequest): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'El Canonical Merge sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/canonical-merge', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function createCanonicalReconciliationJob(payload: CanonicalReconciliationJobRequest): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La reconciliación canónica sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/canonical-reconciliation', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function createCanonicalImportJob(payload: CanonicalImportJobRequest): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La importación canónica sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/canonical-import', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export async function createNeo4jSyncJob(): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La sincronización con Neo4j sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/neo4j-sync', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ batch_size: 200, replace_version: false }),
  })
}

export async function createChromaSyncJob(): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La sincronización con Chroma sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/chroma-sync', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}


export async function createSemanticSyncJob(): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La sincronización semántica sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/semantic-sync', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}


export async function createSemanticInferenceJob(payload: SemanticInferenceJobRequest): Promise<PipelineJobDetail> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La inferencia semántica sólo puede ejecutarse en modo live.')
  return request('/api/admin/pipeline-jobs/semantic-inference', validPipelineJobDetail, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

async function postSemanticReviewAction(semanticId: string, action: string, payload: SemanticReviewRequest | SemanticCorrectionRequest): Promise<SemanticReviewResult> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La revisión semántica sólo puede operar en modo live.')
  return request(`/api/admin/semantic-proposals/${encodeURIComponent(semanticId)}/${action}`, validSemanticReviewResult, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const approveSemanticProposal = (semanticId: string, payload: SemanticReviewRequest) => postSemanticReviewAction(semanticId, 'approve', payload)
export const rejectSemanticProposal = (semanticId: string, payload: SemanticReviewRequest) => postSemanticReviewAction(semanticId, 'reject', payload)
export const correctSemanticProposal = (semanticId: string, payload: SemanticCorrectionRequest) => postSemanticReviewAction(semanticId, 'correct', payload)

export interface StructuralReviewListQuery {
  knowledgeVersionId: string
  status?: string
  entityType?: string
  search?: string
  limit?: number
  offset?: number
}

export async function getStructuralReviewItems(queryInput: StructuralReviewListQuery): Promise<StructuralReviewListResponse> {
  if (dataMode !== 'live') return { items: [], status_counts: {}, total: 0, limit: queryInput.limit ?? 100, offset: queryInput.offset ?? 0, next_offset: null }
  const query = new URLSearchParams({
    knowledge_version_id: queryInput.knowledgeVersionId,
    limit: String(queryInput.limit ?? 100),
    offset: String(queryInput.offset ?? 0),
  })
  if (queryInput.status) query.set('status', queryInput.status)
  if (queryInput.entityType) query.set('entity_type', queryInput.entityType)
  if (queryInput.search?.trim()) query.set('search', queryInput.search.trim())
  return request(`/api/admin/structural-review/items?${query.toString()}`, validStructuralReviewList)
}


export async function getStructuralReviewPackages(candidateVersionId: string): Promise<StructuralReviewPackagesResponse> {
  if (dataMode !== 'live') throw new AdminApiError('not_found', 'Los paquetes de revisión sólo están disponibles en modo live.', 404)
  const query = new URLSearchParams({ changed_only: 'true', limit: '200', offset: '0' })
  return request(`/api/admin/knowledge-versions/${encodeURIComponent(candidateVersionId)}/review-packages?${query.toString()}`, validStructuralReviewPackages)
}

export async function getStructuralReviewItem(itemId: string): Promise<StructuralReviewItemDetail> {
  if (dataMode !== 'live') throw new AdminApiError('not_found', 'La revisión estructural sólo está disponible en modo live.', 404)
  return request(`/api/admin/structural-review/items/${encodeURIComponent(itemId)}`, validStructuralReviewDetail)
}

async function postStructuralReviewAction(itemId: string, action: string, payload: StructuralReviewRequest | StructuralCorrectionRequest): Promise<StructuralReviewResult> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'La revisión estructural sólo puede operar en modo live.')
  return request(`/api/admin/structural-review/items/${encodeURIComponent(itemId)}/${action}`, validStructuralReviewResult, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const approveStructuralReviewItem = (itemId: string, payload: StructuralReviewRequest) => postStructuralReviewAction(itemId, 'approve', payload)
export const rejectStructuralReviewItem = (itemId: string, payload: StructuralReviewRequest) => postStructuralReviewAction(itemId, 'reject', payload)
export const resetStructuralReviewItem = (itemId: string, payload: StructuralReviewRequest) => postStructuralReviewAction(itemId, 'reset', payload)
export const correctStructuralReviewItem = (itemId: string, payload: StructuralCorrectionRequest) => postStructuralReviewAction(itemId, 'correct', payload)


export async function prepareRemovalReview(candidateVersionId: string): Promise<RemovalReviewSet> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'Removal HITL sólo puede operar en modo live.')
  return request(`/api/admin/removal-reconciliation-reviews/${encodeURIComponent(candidateVersionId)}/prepare`, validRemovalReviewSet, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  })
}

export async function getRemovalReview(candidateVersionId: string): Promise<RemovalReviewSet> {
  if (dataMode !== 'live') throw new AdminApiError('not_found', 'Removal HITL sólo está disponible en modo live.', 404)
  return request(`/api/admin/removal-reconciliation-reviews/${encodeURIComponent(candidateVersionId)}`, validRemovalReviewSet)
}

async function postRemovalReviewAction(decisionId: string, action: string, payload: RemovalReviewRequest): Promise<RemovalReviewResult> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'Removal HITL sólo puede operar en modo live.')
  return request(`/api/admin/removal-reconciliation-reviews/decisions/${encodeURIComponent(decisionId)}/${action}`, validRemovalReviewResult, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const confirmRemovalRetain = (decisionId: string, payload: RemovalReviewRequest) => postRemovalReviewAction(decisionId, 'confirm-retain', payload)
export const confirmRemovalRemove = (decisionId: string, payload: RemovalReviewRequest) => postRemovalReviewAction(decisionId, 'confirm-remove', payload)
export const resetRemovalDecision = (decisionId: string, payload: RemovalReviewRequest) => postRemovalReviewAction(decisionId, 'reset', payload)

export async function getRemovalReviewHistory(decisionId: string): Promise<RemovalReviewHistory> {
  if (dataMode !== 'live') throw new AdminApiError('not_found', 'Removal HITL sólo está disponible en modo live.', 404)
  return request(`/api/admin/removal-reconciliation-reviews/decisions/${encodeURIComponent(decisionId)}/history`, validRemovalReviewHistory)
}

export async function getPromotionAssessment(knowledgeVersionId: string): Promise<PromotionAssessment> {
  if (dataMode !== 'live') throw new AdminApiError('not_found', 'Promotion Gate sólo está disponible en modo live.', 404)
  return request(`/api/admin/knowledge-versions/${encodeURIComponent(knowledgeVersionId)}/promotion-assessment`, validPromotionAssessment)
}

export async function promoteKnowledgeVersion(knowledgeVersionId: string, payload: KnowledgeVersionPromoteRequest): Promise<KnowledgeVersionPromotionResult> {
  if (dataMode !== 'live') throw new AdminApiError('http', 'Promotion Gate sólo puede operar en modo live.')
  return request(`/api/admin/knowledge-versions/${encodeURIComponent(knowledgeVersionId)}/promote`, validPromotionResult, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
