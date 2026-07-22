import { demoContexts, demoTree } from '../data/demoSnapshot'
import type { KnowledgeTreeResponse, ScreenReviewContextResponse } from '../types/admin'

export type DataMode = 'demo' | 'live'
export const dataMode: DataMode = import.meta.env.VITE_ADMIN_API_MODE === 'live' ? 'live' : 'demo'

export class AdminApiError extends Error {
  constructor(public readonly kind: 'timeout' | 'network' | 'http' | 'invalid_response' | 'not_found', message: string, public readonly status?: number) { super(message); this.name = 'AdminApiError' }
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const hasString = (value: Record<string, unknown>, key: string) => typeof value[key] === 'string'
const validTree = (value: unknown): value is KnowledgeTreeResponse => isRecord(value) && Array.isArray(value.erps) && value.erps.every((erp) => isRecord(erp) && hasString(erp, 'erp_id') && hasString(erp, 'name') && Array.isArray(erp.modules) && Array.isArray(erp.unassigned_screens))
const validContext = (value: unknown): value is ScreenReviewContextResponse => isRecord(value) && isRecord(value.erp) && isRecord(value.screen) && hasString(value.erp, 'erp_id') && hasString(value.screen, 'screen_id') && isRecord(value.structural_evidence) && Array.isArray(value.semantic_proposals) && Array.isArray(value.review_history) && isRecord(value.traceability) && isRecord(value.navigation) && value.reviewer_identity_verified === false

async function request<T>(path: string, validate: (value: unknown) => value is T): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 8_000)
  try {
    const response = await fetch(path, { signal: controller.signal, headers: { Accept: 'application/json' } })
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
