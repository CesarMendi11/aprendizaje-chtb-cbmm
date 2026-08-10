import { demoContexts, demoTree } from '../data/demoSnapshot'
import type { AdminSystemStatusResponse, CrawlJobRequest, KnowledgeTreeResponse, PipelineJobDetail, PipelineJobListResponse, PipelineJobSummary, ScreenReviewContextResponse } from '../types/admin'

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

const pipelineStatuses = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled'])
const pipelineScopes = new Set(['full', 'screen'])
const validPipelineJobSummary = (value: unknown): value is PipelineJobSummary => {
  if (!isRecord(value)) return false
  return hasString(value, 'id') && hasString(value, 'kind') && hasString(value, 'status') && pipelineStatuses.has(String(value.status)) && hasString(value, 'scope') && pipelineScopes.has(String(value.scope)) && hasString(value, 'request_source') && hasString(value, 'stage') && typeof value.progress_current === 'number' && hasString(value, 'requested_at')
}
const validPipelineJobDetail = (value: unknown): value is PipelineJobDetail => validPipelineJobSummary(value) && isRecord(value) && isRecord(value.parameters) && isRecord(value.checkpoint) && hasString(value, 'created_at') && hasString(value, 'updated_at')
const validPipelineJobList = (value: unknown): value is PipelineJobListResponse => isRecord(value) && Array.isArray(value.items) && value.items.every(validPipelineJobSummary) && typeof value.total === 'number' && typeof value.limit === 'number' && typeof value.offset === 'number'

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

export async function getKnowledgeTree(): Promise<KnowledgeTreeResponse> {
  return dataMode === 'demo' ? Promise.resolve(demoTree) : request('/api/admin/knowledge-tree', validTree)
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


export async function getPipelineJobs(limit = 8): Promise<PipelineJobListResponse> {
  if (dataMode !== 'live') return { items: [], total: 0, limit, offset: 0, next_offset: null }
  return request(`/api/admin/pipeline-jobs?kind=crawl&limit=${encodeURIComponent(String(limit))}`, validPipelineJobList)
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
