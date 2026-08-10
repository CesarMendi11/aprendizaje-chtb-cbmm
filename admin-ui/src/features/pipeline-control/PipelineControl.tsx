import { useCallback, useEffect, useMemo, useState } from 'react'
import { AdminApiError, createCrawlJob, dataMode, getPipelineJob, getPipelineJobs } from '../../api/client'
import type { CrawlJobRequest, PipelineJobDetail, PipelineJobSummary } from '../../types/admin'
import './pipeline-control.css'

const RETENCIONES_ROUTE = '/admin/cuentasxcobrar/retenciones'
const terminalStatuses = new Set(['succeeded', 'failed', 'cancelled'])

type PanelState = {
  active: PipelineJobDetail | null
  recent: PipelineJobSummary[]
  loading: boolean
  launching: boolean
  message: string | null
}

const errorMessage = (error: unknown) => error instanceof AdminApiError ? error.message : 'Ocurrió un error inesperado en el pipeline.'
const labelStatus = (status: string) => ({ queued: 'En cola', running: 'Ejecutando', succeeded: 'Completado', failed: 'Falló', cancelled: 'Cancelado' }[status] ?? status)
const labelStage = (stage: string) => ({ queued: 'En cola', starting: 'Iniciando', loading_profile: 'Cargando perfil', launching_browser: 'Abriendo navegador', logging_in: 'Iniciando sesión', login_succeeded: 'Sesión iniciada', navigating_home: 'Abriendo inicio', navigating_target: 'Abriendo pantalla objetivo', screen_captured: 'Pantalla capturada', exploring_fixed_point: 'Explorando estados seguros', saving_outputs: 'Guardando artefactos', completed: 'Finalizado', failed: 'Falló' }[stage] ?? stage.replaceAll('_', ' '))
const asNumber = (value: unknown) => typeof value === 'number' ? value : 0
const asString = (value: unknown) => typeof value === 'string' ? value : null
const formatTime = (value: string | null) => value ? new Date(value).toLocaleTimeString() : '—'

function JobBadge({ status }: { status: string }) {
  return <span className={`pipeline-badge pipeline-badge--${status}`}><i aria-hidden="true" />{labelStatus(status)}</span>
}

function Counter({ label, value }: { label: string; value: number }) {
  return <div className="pipeline-counter"><strong>{value}</strong><span>{label}</span></div>
}

export function PipelineControl() {
  const [state, setState] = useState<PanelState>({ active: null, recent: [], loading: dataMode === 'live', launching: false, message: null })

  const loadRecent = useCallback(async () => {
    if (dataMode !== 'live') return
    try {
      const response = await getPipelineJobs(8)
      const running = response.items.find((job) => job.status === 'queued' || job.status === 'running') ?? null
      setState((old) => ({ ...old, recent: response.items, loading: false, message: null }))
      if (running) {
        const detail = await getPipelineJob(running.id)
        setState((old) => ({ ...old, active: detail }))
      }
    } catch (error: unknown) {
      setState((old) => ({ ...old, loading: false, message: errorMessage(error) }))
    }
  }, [])

  useEffect(() => { void loadRecent() }, [loadRecent])

  const activeId = state.active?.id ?? null
  const activeTerminal = state.active ? terminalStatuses.has(state.active.status) : true
  useEffect(() => {
    if (dataMode !== 'live' || !activeId || activeTerminal) return
    const timer = window.setInterval(async () => {
      try {
        const detail = await getPipelineJob(activeId)
        setState((old) => ({ ...old, active: detail, message: null }))
        if (terminalStatuses.has(detail.status)) void loadRecent()
      } catch (error: unknown) {
        setState((old) => ({ ...old, message: errorMessage(error) }))
      }
    }, 1_000)
    return () => window.clearInterval(timer)
  }, [activeId, activeTerminal, loadRecent])

  const hasRunningJob = state.recent.some((item) => item.status === 'queued' || item.status === 'running')
  const isBusy = state.launching || hasRunningJob || Boolean(state.active && !terminalStatuses.has(state.active.status))

  const launch = useCallback(async (payload: CrawlJobRequest) => {
    if (dataMode !== 'live' || isBusy) return
    setState((old) => ({ ...old, launching: true, message: null }))
    try {
      const job = await createCrawlJob(payload)
      setState((old) => ({ ...old, active: job, launching: false, recent: [job, ...old.recent.filter((item) => item.id !== job.id)].slice(0, 8) }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, launching: false, message: errorMessage(error) }))
    }
  }, [isBusy])

  const launchRetenciones = () => void launch({ scope: 'screen', target: RETENCIONES_ROUTE, headless: false, slow_mo: 100 })
  const launchFull = () => {
    if (!window.confirm('El recorrido completo explora el ERP y puede tardar varios minutos. ¿Desea iniciarlo?')) return
    void launch({ scope: 'full', target: null, headless: false, slow_mo: 100 })
  }

  const job = state.active
  const checkpoint = job?.checkpoint ?? {}
  const metrics = useMemo(() => ({
    routes: asNumber(checkpoint.routes_visited),
    pendingRoutes: asNumber(checkpoint.routes_pending),
    screens: asNumber(checkpoint.functional_screens),
    states: asNumber(checkpoint.ui_states),
    transitions: asNumber(checkpoint.ui_transitions),
    pendingStates: asNumber(checkpoint.states_pending),
  }), [checkpoint])
  const artifactRoot = asString(job?.result_payload?.artifact_root)

  return <section className="pipeline-console" aria-label="Control del pipeline de descubrimiento">
    <div className="pipeline-console__heading">
      <div><span className="pipeline-eyebrow">Pipeline operativo</span><h2>Descubrimiento estructural</h2><p>Ejecuta el crawler con artefactos aislados. Los runs de demostración no reemplazan el snapshot oficial.</p></div>
      <div className="pipeline-actions">
        <button className="pipeline-primary" onClick={launchRetenciones} disabled={dataMode !== 'live' || isBusy}>Recorrer Retenciones</button>
        <button onClick={launchFull} disabled={dataMode !== 'live' || isBusy}>Recorrer ERP completo</button>
        <button className="pipeline-refresh" onClick={() => void loadRecent()} disabled={dataMode !== 'live' || state.loading}>Actualizar jobs</button>
      </div>
    </div>

    {dataMode !== 'live' && <div className="pipeline-notice">Modo demostración: los procesos están desactivados. Inicia la Admin UI con <code>VITE_ADMIN_API_MODE=live</code> para operar el pipeline.</div>}
    {state.message && <div className="pipeline-error" role="alert">{state.message}</div>}

    <div className="pipeline-grid">
      <article className="pipeline-active">
        <div className="pipeline-card-head"><div><span>Job activo / último job</span><h3>{job ? (job.scope === 'screen' ? 'Crawler · Retenciones' : 'Crawler · ERP completo') : 'Sin ejecuciones'}</h3></div>{job && <JobBadge status={job.status} />}</div>
        {!job ? <p className="pipeline-empty">Ejecuta Retenciones para realizar una demostración corta y aislada del crawler.</p> : <>
          <div className="pipeline-job-meta"><span>Etapa <strong>{labelStage(job.stage)}</strong></span><span>Inicio <strong>{formatTime(job.started_at)}</strong></span><span>Fin <strong>{formatTime(job.finished_at)}</strong></span></div>
          <div className="pipeline-progress"><div className={`pipeline-progress__bar pipeline-progress__bar--${job.status}`}><span /></div><small>{job.status === 'running' ? `Unidades procesadas: ${job.progress_current} · progreso total no estimado` : labelStatus(job.status)}</small></div>
          <div className="pipeline-counters"><Counter label="Rutas" value={metrics.routes}/><Counter label="Pantallas" value={metrics.screens}/><Counter label="Estados UI" value={metrics.states}/><Counter label="Transiciones" value={metrics.transitions}/><Counter label="Pend. rutas" value={metrics.pendingRoutes}/><Counter label="Pend. estados" value={metrics.pendingStates}/></div>
          {job.target && <p className="pipeline-target"><span>Objetivo</span><code>{job.target}</code></p>}
          {job.error_summary && <p className="pipeline-job-error">{job.error_summary}</p>}
          {artifactRoot && <p className="pipeline-artifacts"><span>Artefactos aislados</span><code>{artifactRoot}</code></p>}
        </>}
      </article>

      <article className="pipeline-history">
        <div className="pipeline-card-head"><div><span>Trazabilidad</span><h3>Ejecuciones recientes</h3></div><strong>{state.recent.length}</strong></div>
        {state.recent.length === 0 ? <p className="pipeline-empty">Todavía no hay jobs de crawling registrados.</p> : <div className="pipeline-job-list">{state.recent.slice(0, 6).map((item) => <button key={item.id} disabled={Boolean(job && !terminalStatuses.has(job.status) && item.id !== job.id)} onClick={() => void getPipelineJob(item.id).then((detail) => setState((old) => ({ ...old, active: detail }))).catch((error: unknown) => setState((old) => ({ ...old, message: errorMessage(error) })))} className={item.id === job?.id ? 'is-selected' : ''}><div><strong>{item.scope === 'screen' ? 'Retenciones' : 'ERP completo'}</strong><span>{new Date(item.requested_at).toLocaleString()}</span></div><JobBadge status={item.status} /></button>)}</div>}
      </article>
    </div>
  </section>
}
