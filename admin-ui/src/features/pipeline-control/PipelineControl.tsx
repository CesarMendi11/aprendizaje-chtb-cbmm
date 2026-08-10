import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AdminApiError,
  createCanonicalBuildJob,
  createCanonicalImportJob,
  createChromaSyncJob,
  createCrawlJob,
  createNeo4jSyncJob,
  dataMode,
  getPipelineJob,
  getPipelineJobs,
  getSystemStatus,
} from '../../api/client'
import type { AdminSystemStatusResponse, CrawlJobRequest, PipelineJobDetail, PipelineJobSummary } from '../../types/admin'
import './pipeline-control.css'

const RETENCIONES_ROUTE = '/admin/cuentasxcobrar/retenciones'
const terminalStatuses = new Set(['succeeded', 'failed', 'cancelled'])

type PanelState = {
  active: PipelineJobDetail | null
  recent: PipelineJobSummary[]
  system: AdminSystemStatusResponse | null
  loading: boolean
  launching: boolean
  message: string | null
}

const errorMessage = (error: unknown) => error instanceof AdminApiError ? error.message : 'Ocurrió un error inesperado en el pipeline.'
const labelStatus = (status: string) => ({ queued: 'En cola', running: 'Ejecutando', succeeded: 'Completado', failed: 'Falló', cancelled: 'Cancelado' }[status] ?? status)
const labelStage = (stage: string) => ({
  queued: 'En cola',
  starting: 'Iniciando',
  loading_profile: 'Cargando perfil',
  launching_browser: 'Abriendo navegador',
  logging_in: 'Iniciando sesión',
  login_succeeded: 'Sesión iniciada',
  navigating_home: 'Abriendo inicio',
  navigating_target: 'Abriendo pantalla objetivo',
  screen_captured: 'Pantalla capturada',
  exploring_fixed_point: 'Explorando estados seguros',
  saving_outputs: 'Guardando artefactos',
  loading_crawl_artifacts: 'Cargando artefactos del crawl',
  building_canonical: 'Construyendo conocimiento canónico',
  validating_canonical: 'Validando conocimiento canónico',
  exporting_canonical: 'Exportando canonical',
  loading_canonical: 'Cargando canonical',
  validating_import: 'Validando importación',
  importing_staging: 'Importando a staging',
  staging_ready: 'Staging listo',
  validating_active_version: 'Verificando versión activa',
  projection_planned: 'Proyección preparada',
  syncing_neo4j: 'Sincronizando Neo4j',
  neo4j_synced: 'Neo4j sincronizado',
  documents_prepared: 'Documentos preparados',
  embedding_and_syncing: 'Generando embeddings y sincronizando',
  chroma_synced: 'Chroma sincronizado',
  validating_active_screen: 'Verificando pantalla activa',
  evidence_prepared: 'Evidencia preparada',
  generating_semantic_proposal: 'Generando propuesta con Ollama',
  proposal_ready: 'Propuesta semántica lista',
  completed: 'Finalizado',
  failed: 'Falló',
}[stage] ?? stage.replaceAll('_', ' '))
const asNumber = (value: unknown) => typeof value === 'number' ? value : 0
const asString = (value: unknown) => typeof value === 'string' ? value : null
const asBoolean = (value: unknown) => typeof value === 'boolean' ? value : false
const formatTime = (value: string | null) => value ? new Date(value).toLocaleTimeString() : '—'

const kindLabel = (kind: string) => ({
  crawl: 'Crawler',
  canonical_build: 'Canonical Builder',
  canonical_import: 'Importación staging',
  neo4j_sync: 'Sync Neo4j',
  chroma_sync: 'Sync Chroma',
  semantic_inference: 'Inferencia semántica',
}[kind] ?? kind.replaceAll('_', ' '))

const targetLabel = (job: PipelineJobSummary | PipelineJobDetail) => {
  if (job.scope === 'screen') return 'Retenciones'
  if (job.scope === 'full') return 'ERP completo'
  if (job.scope === 'version') return job.target ? `Versión ${job.target}` : 'Versión activa'
  return job.target ?? 'Sistema'
}

const jobTitle = (job: PipelineJobSummary | PipelineJobDetail) => `${kindLabel(job.kind)} · ${targetLabel(job)}`

function JobBadge({ status }: { status: string }) {
  return <span className={`pipeline-badge pipeline-badge--${status}`}><i aria-hidden="true" />{labelStatus(status)}</span>
}

function Counter({ label, value }: { label: string; value: number | string }) {
  return <div className="pipeline-counter"><strong>{value}</strong><span>{label}</span></div>
}

function ServiceState({ status }: { status: string }) {
  return <span className={`projection-service-state projection-service-state--${status}`}><i aria-hidden="true" />{status}</span>
}

export function PipelineControl() {
  const [state, setState] = useState<PanelState>({ active: null, recent: [], system: null, loading: dataMode === 'live', launching: false, message: null })

  const loadRecent = useCallback(async () => {
    if (dataMode !== 'live') return
    try {
      const response = await getPipelineJobs(20)
      let system: AdminSystemStatusResponse | null = null
      try { system = await getSystemStatus() } catch { /* Los jobs siguen siendo operables aunque falle este resumen. */ }
      const running = response.items.find((job) => job.status === 'queued' || job.status === 'running') ?? null
      setState((old) => ({ ...old, recent: response.items, system: system ?? old.system, loading: false, message: null }))
      if (running) {
        const detail = await getPipelineJob(running.id)
        setState((old) => ({ ...old, active: detail }))
      } else {
        setState((old) => old.active ? old : { ...old, active: null })
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

  const rememberJob = (job: PipelineJobDetail) => {
    setState((old) => ({ ...old, active: job, launching: false, recent: [job, ...old.recent.filter((item) => item.id !== job.id)].slice(0, 20) }))
  }

  const launchCrawl = useCallback(async (payload: CrawlJobRequest) => {
    if (dataMode !== 'live' || isBusy) return
    setState((old) => ({ ...old, launching: true, message: null }))
    try { rememberJob(await createCrawlJob(payload)) }
    catch (error: unknown) { setState((old) => ({ ...old, launching: false, message: errorMessage(error) })) }
  }, [isBusy])

  const launchRetenciones = () => void launchCrawl({ scope: 'screen', target: RETENCIONES_ROUTE, headless: false, slow_mo: 100 })
  const launchFull = () => {
    if (!window.confirm('El recorrido completo explora el ERP y puede tardar varios minutos. ¿Desea iniciarlo?')) return
    void launchCrawl({ scope: 'full', target: null, headless: false, slow_mo: 100 })
  }

  const launchCanonicalBuild = async () => {
    const source = state.active
    if (dataMode !== 'live' || isBusy || !source || source.kind !== 'crawl' || source.status !== 'succeeded') return
    setState((old) => ({ ...old, launching: true, message: null }))
    try { rememberJob(await createCanonicalBuildJob({ source_crawl_job_id: source.id })) }
    catch (error: unknown) { setState((old) => ({ ...old, launching: false, message: errorMessage(error) })) }
  }

  const launchCanonicalImport = async () => {
    const source = state.active
    if (dataMode !== 'live' || isBusy || !source || source.kind !== 'canonical_build' || source.status !== 'succeeded') return
    setState((old) => ({ ...old, launching: true, message: null }))
    try { rememberJob(await createCanonicalImportJob({ source_canonical_job_id: source.id })) }
    catch (error: unknown) { setState((old) => ({ ...old, launching: false, message: errorMessage(error) })) }
  }

  const launchProjection = async (kind: 'neo4j_sync' | 'chroma_sync') => {
    if (dataMode !== 'live' || isBusy) return
    setState((old) => ({ ...old, launching: true, message: null }))
    try {
      rememberJob(kind === 'neo4j_sync' ? await createNeo4jSyncJob() : await createChromaSyncJob())
    } catch (error: unknown) {
      setState((old) => ({ ...old, launching: false, message: errorMessage(error) }))
    }
  }

  const selectJob = async (jobId: string) => {
    try {
      const detail = await getPipelineJob(jobId)
      setState((old) => ({ ...old, active: detail, message: null }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, message: errorMessage(error) }))
    }
  }

  const job = state.active
  const checkpoint = job?.checkpoint ?? {}
  const result = job?.result_payload ?? {}
  const crawlMetrics = useMemo(() => ({
    routes: asNumber(checkpoint.routes_visited),
    pendingRoutes: asNumber(checkpoint.routes_pending),
    screens: asNumber(checkpoint.functional_screens),
    states: asNumber(checkpoint.ui_states),
    transitions: asNumber(checkpoint.ui_transitions),
    pendingStates: asNumber(checkpoint.states_pending),
  }), [checkpoint])

  const statistics = result.statistics && typeof result.statistics === 'object' && !Array.isArray(result.statistics)
    ? result.statistics as Record<string, unknown>
    : {}
  const artifactRoot = asString(result.artifact_root)
  const canonicalDir = asString(result.canonical_dir)
  const knowledgeVersion = asString(result.knowledge_version) ?? asString(checkpoint.knowledge_version)
  const canBuild = Boolean(job && job.kind === 'crawl' && job.status === 'succeeded' && !isBusy)
  const canImport = Boolean(job && job.kind === 'canonical_build' && job.status === 'succeeded' && !isBusy)

  const system = state.system
  const activeVersion = system?.knowledge.active_version ?? null
  const postgresqlOnline = system?.services.postgresql.status === 'online'
  const neo4jOnline = system?.services.neo4j.status === 'online'
  const chromaReady = system?.services.chroma.status === 'ready'
  const ollamaReady = system?.services.ollama.status === 'online' && system.services.ollama.configured_embedding_model_available === true
  const canSyncNeo4j = Boolean(dataMode === 'live' && !isBusy && activeVersion && postgresqlOnline && neo4jOnline)
  const canSyncChroma = Boolean(dataMode === 'live' && !isBusy && activeVersion && postgresqlOnline && chromaReady && ollamaReady)
  const latestNeo4j = state.recent.find((item) => item.kind === 'neo4j_sync') ?? null
  const latestChroma = state.recent.find((item) => item.kind === 'chroma_sync') ?? null

  return <section className="pipeline-console" aria-label="Control del pipeline de conocimiento">
    <div className="pipeline-console__heading">
      <div><span className="pipeline-eyebrow">Pipeline operativo</span><h2>Construcción de conocimiento</h2><p>Ejecuta crawling, canonicalización e importación staging con trazabilidad persistente. Los runs cortos no reemplazan la versión activa del ERP.</p></div>
      <div className="pipeline-actions">
        <button className="pipeline-primary" onClick={launchRetenciones} disabled={dataMode !== 'live' || isBusy}>Recorrer Retenciones</button>
        <button onClick={launchFull} disabled={dataMode !== 'live' || isBusy}>Recorrer ERP completo</button>
        <button className="pipeline-next" onClick={() => void launchCanonicalBuild()} disabled={dataMode !== 'live' || !canBuild}>Construir canonical</button>
        <button className="pipeline-next" onClick={() => void launchCanonicalImport()} disabled={dataMode !== 'live' || !canImport}>Importar a staging</button>
        <button className="pipeline-refresh" onClick={() => void loadRecent()} disabled={dataMode !== 'live' || state.loading}>Actualizar jobs</button>
      </div>
    </div>

    {dataMode !== 'live' && <div className="pipeline-notice">Modo demostración: los procesos están desactivados. Inicia la Admin UI con <code>VITE_ADMIN_API_MODE=live</code> para operar el pipeline.</div>}
    {state.message && <div className="pipeline-error" role="alert">{state.message}</div>}

    <div className="pipeline-flow" aria-label="Etapas habilitadas">
      <span>Crawler</span><i>→</i><span>Canonical Builder</span><i>→</i><span>PostgreSQL staging</span>
    </div>

    <div className="pipeline-grid">
      <article className="pipeline-active">
        <div className="pipeline-card-head"><div><span>Job seleccionado</span><h3>{job ? jobTitle(job) : 'Sin ejecuciones seleccionadas'}</h3></div>{job && <JobBadge status={job.status} />}</div>
        {!job ? <p className="pipeline-empty">Ejecuta Retenciones para iniciar una demostración corta del pipeline o selecciona un job reciente.</p> : <>
          <div className="pipeline-job-meta"><span>Etapa <strong>{labelStage(job.stage)}</strong></span><span>Inicio <strong>{formatTime(job.started_at)}</strong></span><span>Fin <strong>{formatTime(job.finished_at)}</strong></span></div>
          <div className="pipeline-progress"><div className={`pipeline-progress__bar pipeline-progress__bar--${job.status}`}><span style={job.progress_percent !== null && job.status !== 'running' ? { width: `${job.progress_percent}%` } : undefined} /></div><small>{job.progress_total ? `${job.progress_current} / ${job.progress_total} · ${job.progress_percent ?? 0}%` : job.status === 'running' ? `Unidades procesadas: ${job.progress_current} · progreso total no estimado` : labelStatus(job.status)}</small></div>

          {job.kind === 'crawl' && <div className="pipeline-counters"><Counter label="Rutas" value={crawlMetrics.routes}/><Counter label="Pantallas" value={crawlMetrics.screens}/><Counter label="Estados UI" value={crawlMetrics.states}/><Counter label="Transiciones" value={crawlMetrics.transitions}/><Counter label="Pend. rutas" value={crawlMetrics.pendingRoutes}/><Counter label="Pend. estados" value={crawlMetrics.pendingStates}/></div>}

          {job.kind === 'canonical_build' && <div className="pipeline-counters pipeline-counters--canonical"><Counter label="Pantallas" value={asNumber(statistics.screens)}/><Counter label="Campos" value={asNumber(statistics.fields)}/><Counter label="Controles" value={asNumber(statistics.controls)}/><Counter label="Tablas" value={asNumber(statistics.tables)}/><Counter label="Columnas" value={asNumber(statistics.table_columns)}/><Counter label="Errores" value={asNumber(result.validation_errors)}/></div>}

          {job.kind === 'canonical_import' && <div className="pipeline-counters pipeline-counters--import"><Counter label="Items" value={asNumber(result.items)}/><Counter label="Reviews heredadas" value={asNumber(result.carried_reviews)}/><Counter label="Estado" value={asString(result.version_status) ?? '—'}/><Counter label="Sync jobs" value={asNumber(result.sync_jobs_present)}/><Counter label="Activó versión" value={asBoolean(result.activation_performed) ? 'Sí' : 'No'}/><Counter label="Staging" value={asBoolean(result.staging_ready) ? 'Listo' : '—'}/></div>}

          {job.kind === 'neo4j_sync' && <div className="pipeline-counters pipeline-counters--projection"><Counter label="Elegibles" value={asNumber(result.eligible_items) || asNumber(checkpoint.eligible_items)}/><Counter label="Nodos" value={asNumber(result.nodes) || asNumber(checkpoint.nodes)}/><Counter label="Relaciones" value={asNumber(result.relationships) || asNumber(checkpoint.relationships)}/><Counter label="Rel. omitidas" value={asNumber(result.skipped_relationships)}/><Counter label="Reemplazo" value={asBoolean(result.replace_version) ? 'Sí' : 'No'}/><Counter label="Active only" value={asBoolean(result.active_only) ? 'Sí' : '—'}/></div>}

          {job.kind === 'chroma_sync' && <div className="pipeline-counters pipeline-counters--projection"><Counter label="Elegibles" value={asNumber(result.eligible_items) || asNumber(checkpoint.eligible_items)}/><Counter label="Documentos" value={asNumber(result.documents) || asNumber(checkpoint.documents)}/><Counter label="Actualizados" value={asNumber(result.inserted_or_updated) || asNumber(checkpoint.inserted_or_updated)}/><Counter label="Stale removidos" value={asNumber(result.removed_stale)}/><Counter label="Dimensiones" value={asNumber(result.embedding_dimensions)}/><Counter label="Omitidos" value={asNumber(result.skipped)}/></div>}

          {job.kind === 'semantic_inference' && <div className="pipeline-counters pipeline-counters--projection"><Counter label="Capabilities" value={asNumber(result.capabilities)}/><Counter label="Estado propuesta" value={asString(result.proposal_status) ?? '—'}/><Counter label="Creada" value={asBoolean(result.created) ? 'Sí' : 'No'}/><Counter label="Ollama" value={asBoolean(result.ollama_called) ? 'Ejecutado' : 'Reutilizado'}/><Counter label="Prompt" value={asString(result.prompt_version) ?? asString(checkpoint.prompt_version) ?? '—'}/><Counter label="Modelo" value={asString(result.generation_model) ?? asString(checkpoint.generation_model) ?? '—'}/></div>}

          {job.target && <p className="pipeline-target"><span>Objetivo</span><code>{job.target}</code></p>}
          {knowledgeVersion && <p className="pipeline-target"><span>Knowledge version</span><code>{knowledgeVersion}</code></p>}
          {job.kind === 'chroma_sync' && asString(result.embedding_model) && <p className="pipeline-target"><span>Embedding model</span><code>{asString(result.embedding_model)}</code></p>}
          {job.error_summary && <p className="pipeline-job-error">{job.error_summary}</p>}
          {artifactRoot && <p className="pipeline-artifacts"><span>Artefactos aislados</span><code>{artifactRoot}</code></p>}
          {canonicalDir && <p className="pipeline-artifacts"><span>Canonical</span><code>{canonicalDir}</code></p>}
          {canBuild && <div className="pipeline-next-step"><span>Siguiente etapa disponible</span><button onClick={() => void launchCanonicalBuild()}>Construir canonical desde este crawl</button></div>}
          {canImport && <div className="pipeline-next-step"><span>Siguiente etapa disponible</span><button onClick={() => void launchCanonicalImport()}>Importar este canonical a staging</button></div>}
        </>}
      </article>

      <article className="pipeline-history">
        <div className="pipeline-card-head"><div><span>Trazabilidad</span><h3>Ejecuciones recientes</h3></div><strong>{state.recent.length}</strong></div>
        {state.recent.length === 0 ? <p className="pipeline-empty">Todavía no hay jobs registrados.</p> : <div className="pipeline-job-list">{state.recent.slice(0, 12).map((item) => <button key={item.id} disabled={Boolean(job && !terminalStatuses.has(job.status) && item.id !== job.id)} onClick={() => void selectJob(item.id)} className={item.id === job?.id ? 'is-selected' : ''}><div><strong>{jobTitle(item)}</strong><span>{new Date(item.requested_at).toLocaleString()} · {labelStage(item.stage)}</span></div><JobBadge status={item.status} /></button>)}</div>}
      </article>
    </div>

    <div className="pipeline-projections" aria-label="Proyecciones de la versión activa">
      <div className="pipeline-projections__heading">
        <div><span className="pipeline-eyebrow">Publicación controlada</span><h3>Proyecciones de la versión ACTIVE</h3><p>PostgreSQL sigue siendo la fuente de verdad. Estos controles sólo capturan y verifican la versión activa; la versión staging no puede seleccionarse aquí.</p></div>
        <div className="projection-active-version"><span>Versión activa</span><code>{activeVersion ?? 'No disponible'}</code></div>
      </div>

      <div className="projection-grid">
        <article className="projection-card projection-card--source">
          <div className="projection-card__head"><div><span>Fuente de verdad</span><h4>PostgreSQL</h4></div><ServiceState status={system?.services.postgresql.status ?? 'unknown'} /></div>
          <strong className="projection-main-value">{system?.knowledge.approved ?? '—'}</strong><span className="projection-main-label">items aprobados en ACTIVE</span>
          <dl><div><dt>Pendientes</dt><dd>{system?.knowledge.pending_review ?? '—'}</dd></div><div><dt>Total</dt><dd>{system?.knowledge.total_items ?? '—'}</dd></div></dl>
        </article>

        <article className="projection-card">
          <div className="projection-card__head"><div><span>Grafo aprobado</span><h4>Neo4j</h4></div><ServiceState status={system?.services.neo4j.status ?? 'unknown'} /></div>
          <div className="projection-pair"><div><strong>{system?.services.neo4j.nodes ?? '—'}</strong><span>Nodos</span></div><div><strong>{system?.services.neo4j.relationships ?? '—'}</strong><span>Relaciones</span></div></div>
          <p>Upsert no destructivo desde la versión ACTIVE; <code>replace_version=false</code>.</p>
          <div className="projection-action-row"><button onClick={() => void launchProjection('neo4j_sync')} disabled={!canSyncNeo4j}>Sincronizar Neo4j</button>{latestNeo4j && <button className="projection-job-link" onClick={() => void selectJob(latestNeo4j.id)}>Ver último job</button>}</div>
          {latestNeo4j && <div className="projection-last"><span>Última ejecución</span><JobBadge status={latestNeo4j.status} /></div>}
        </article>

        <article className="projection-card">
          <div className="projection-card__head"><div><span>Índice semántico</span><h4>Chroma</h4></div><ServiceState status={system?.services.chroma.status ?? 'unknown'} /></div>
          <strong className="projection-main-value">{system?.services.chroma.documents ?? '—'}</strong><span className="projection-main-label">documentos recuperables</span>
          <p>Embeddings con <code>{system?.services.ollama.configured_embedding_model ?? 'modelo no disponible'}</code>. Ollama: {system?.services.ollama.status ?? 'unknown'}.</p>
          <div className="projection-action-row"><button onClick={() => void launchProjection('chroma_sync')} disabled={!canSyncChroma}>Sincronizar Chroma</button>{latestChroma && <button className="projection-job-link" onClick={() => void selectJob(latestChroma.id)}>Ver último job</button>}</div>
          {latestChroma && <div className="projection-last"><span>Última ejecución</span><JobBadge status={latestChroma.status} /></div>}
        </article>
      </div>
    </div>
  </section>
}
