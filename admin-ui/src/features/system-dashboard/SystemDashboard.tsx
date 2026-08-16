import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AdminApiError, createChromaSyncJob, createNeo4jSyncJob, getSystemStatus } from '../../api/client'
import type { AdminSystemStatusResponse } from '../../types/admin'
import './system-dashboard.css'

type DashboardState =
  | { status: 'loading'; data?: AdminSystemStatusResponse }
  | { status: 'ready'; data: AdminSystemStatusResponse }
  | { status: 'error'; message: string; data?: AdminSystemStatusResponse }

const messageOf = (error: unknown) =>
  error instanceof AdminApiError ? error.message : 'No fue posible actualizar la observabilidad.'

const stateLabel = (status: string) => {
  if (status === 'online') return 'Online'
  if (status === 'ready') return 'Ready'
  if (status === 'offline') return 'Offline'
  if (status === 'unavailable') return 'No disponible'
  if (status === 'succeeded') return 'Sucedido'
  return status
}

const stateTone = (status: string) =>
  ['online', 'ready', 'succeeded'].includes(status)
    ? 'ok'
    : ['offline', 'unavailable', 'failed'].includes(status)
      ? 'bad'
      : 'warn'

function ServiceCard({ name, status, children }: { name: string; status: string; children: ReactNode }) {
  return <article className="system-service-card">
    <div className="system-service-card__head"><h3>{name}</h3><span className={`system-state system-state--${stateTone(status)}`}><i aria-hidden="true" />{stateLabel(status)}</span></div>
    <div className="system-service-card__body">{children}</div>
  </article>
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="system-metric"><span>{label}</span><strong>{value}</strong></div>
}

export function SystemDashboard() {
  const [state, setState] = useState<DashboardState>({ status: 'loading' })
  const [retryingSyncId, setRetryingSyncId] = useState<string | null>(null)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const load = useCallback(async () => {
    setState((old) => ({ status: 'loading', data: old.data }))
    try { setState({ status: 'ready', data: await getSystemStatus() }) }
    catch (error: unknown) { setState((old) => ({ status: 'error', message: messageOf(error), data: old.data })) }
  }, [])

  const retrySync = useCallback(async (job: AdminSystemStatusResponse['knowledge']['sync_jobs'][number]) => {
    if (job.status !== 'failed' || retryingSyncId) return
    setRetryingSyncId(job.id)
    setSyncMessage(null)
    try {
      if (job.target === 'neo4j') await createNeo4jSyncJob()
      else if (job.target === 'chromadb') await createChromaSyncJob()
      else throw new AdminApiError('http', `Target de sincronización no soportado: ${job.target}`)
      setSyncMessage(`Reintento ${job.target} encolado.`)
      await load()
    } catch (error: unknown) {
      setSyncMessage(messageOf(error))
    } finally {
      setRetryingSyncId(null)
    }
  }, [load, retryingSyncId])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 15_000)
    return () => window.clearInterval(timer)
  }, [load])

  if (!state.data) return <section className="system-dashboard system-dashboard--empty" aria-live="polite"><div><strong>Estado del sistema</strong><span>{state.status === 'error' ? state.message : 'Consultando servicios…'}</span></div>{state.status === 'error' && <button onClick={() => void load()}>Reintentar</button>}</section>

  const data = state.data
  const { postgresql, neo4j, chroma, ollama } = data.services
  const syncJobs = data.knowledge.sync_jobs.slice(0, 4)

  return <section className="system-dashboard" aria-label="Estado operativo del prototipo">
    <div className="system-dashboard__heading">
      <div><span className="system-eyebrow">Observabilidad live</span><h2>Estado del sistema</h2><p>Última lectura: {new Date(data.generated_at).toLocaleTimeString()}{state.status === 'error' ? ` · ${state.message}` : ''}</p></div>
      <div className="system-dashboard__actions"><span className={`system-overall system-overall--${data.ok ? 'ok' : 'degraded'}`}>{data.ok ? 'Sistema operativo' : 'Sistema degradado'}</span><button onClick={() => void load()} disabled={state.status === 'loading'}>{state.status === 'loading' ? 'Actualizando…' : 'Actualizar estado'}</button></div>
    </div>
    <div className="system-services">
      <ServiceCard name="PostgreSQL" status={postgresql.status}><span>Versión activa</span><strong>{postgresql.active_version ?? 'Sin versión activa'}</strong></ServiceCard>
      <ServiceCard name="Neo4j" status={neo4j.status}><span>{neo4j.nodes ?? 0} nodos · {neo4j.relationships ?? 0} relaciones</span><strong>{neo4j.server_agent ?? neo4j.detail ?? 'Sin información del servidor'}</strong></ServiceCard>
      <ServiceCard name="ChromaDB" status={chroma.status}><span>{chroma.documents ?? 0} documentos</span><strong>{chroma.collection ?? chroma.detail ?? 'Sin colección disponible'}</strong></ServiceCard>
      <ServiceCard name="Ollama" status={ollama.status}><span>{ollama.models?.length ?? 0} modelos disponibles</span><strong>{ollama.configured_embedding_model ?? ollama.detail ?? 'Sin modelo configurado'}</strong></ServiceCard>
    </div>
    <div className="system-lower">
      <div className="system-knowledge"><div className="system-section-title"><div><span>Fuente de verdad</span><h3>Conocimiento activo</h3></div><code>{data.knowledge.active_version ?? 'sin-versión'}</code></div><div className="system-metrics"><Metric label="Items" value={data.knowledge.total_items}/><Metric label="Aprobados" value={data.knowledge.approved}/><Metric label="Pendientes" value={data.knowledge.pending_review}/><Metric label="Corregidos" value={data.knowledge.corrected}/><Metric label="Rechazados" value={data.knowledge.rejected}/></div></div>
      <div className="system-syncs"><div className="system-section-title"><div><span>Proyecciones gobernadas</span><h3>Sincronizaciones</h3></div></div>{syncMessage && <p className="system-sync-message">{syncMessage}</p>}{syncJobs.length === 0 ? <p className="system-empty">No existen sincronizaciones registradas para la versión activa.</p> : <div className="system-sync-list">{syncJobs.map((job) => <div className="system-sync-row" key={job.id}><div className="system-sync-copy"><strong>{job.target}</strong><span>Intento {job.attempt_count}</span>{job.error_summary && <small>{job.error_summary}</small>}</div><div className="system-sync-actions"><span className={`system-state system-state--${stateTone(job.status)}`}><i aria-hidden="true" />{stateLabel(job.status)}</span>{job.status === 'failed' && ['neo4j', 'chromadb'].includes(job.target) && <button onClick={() => void retrySync(job)} disabled={retryingSyncId !== null}>{retryingSyncId === job.id ? 'Encolando…' : 'Reintentar'}</button>}</div></div>)}</div>}</div>
    </div>
  </section>
}
