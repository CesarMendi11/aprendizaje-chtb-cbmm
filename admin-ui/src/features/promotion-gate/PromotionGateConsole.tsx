import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AdminApiError,
  getPipelineJobs,
  getPromotionAssessment,
  promoteKnowledgeVersion,
} from '../../api/client'
import type {
  KnowledgeVersionPromotionResult,
  PipelineJobSummary,
  PromotionAssessment,
} from '../../types/admin'
import './promotion-gate.css'

type State = {
  imports: PipelineJobSummary[]
  versionId: string | null
  assessment: PromotionAssessment | null
  result: KnowledgeVersionPromotionResult | null
  loading: boolean
  submitting: boolean
  message: string | null
}

const errorMessage = (error: unknown) =>
  error instanceof AdminApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : 'Ocurrió un error inesperado en Promotion Gate.'

const when = (value: string) => new Date(value).toLocaleString()

export function PromotionGateConsole({ onPromoted }: { onPromoted?: () => void | Promise<void> }) {
  const [state, setState] = useState<State>({
    imports: [],
    versionId: null,
    assessment: null,
    result: null,
    loading: true,
    submitting: false,
    message: null,
  })
  const [reviewerId, setReviewerId] = useState('')
  const [reason, setReason] = useState('')

  const loadImports = useCallback(async () => {
    setState((old) => ({ ...old, loading: true, message: null }))
    try {
      const response = await getPipelineJobs(100, 'canonical_import')
      const imports = response.items.filter(
        (job) => job.status === 'succeeded' && Boolean(job.knowledge_version_id),
      )
      setState((old) => ({
        ...old,
        imports,
        versionId: old.versionId ?? imports[0]?.knowledge_version_id ?? null,
        loading: false,
      }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, loading: false, message: errorMessage(error) }))
    }
  }, [])

  const loadAssessment = useCallback(async (versionId: string) => {
    setState((old) => ({ ...old, loading: true, message: null, result: null }))
    try {
      const assessment = await getPromotionAssessment(versionId)
      setState((old) => ({ ...old, assessment, loading: false }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, assessment: null, loading: false, message: errorMessage(error) }))
    }
  }, [])

  useEffect(() => { void loadImports() }, [loadImports])
  useEffect(() => {
    if (!state.versionId) return
    void loadAssessment(state.versionId)
  }, [loadAssessment, state.versionId])

  const selectedImport = useMemo(
    () => state.imports.find((job) => job.knowledge_version_id === state.versionId) ?? null,
    [state.imports, state.versionId],
  )

  const promote = async () => {
    const assessment = state.assessment
    if (!assessment || !assessment.promotable || !reviewerId.trim() || !reason.trim() || state.submitting) {
      return
    }
    setState((old) => ({ ...old, submitting: true, message: null }))
    try {
      const result = await promoteKnowledgeVersion(assessment.knowledge_version_id, {
        reviewer_id: reviewerId.trim(),
        reason: reason.trim(),
        expected_knowledge_version: assessment.knowledge_version,
        confirm_promotion: true,
      })
      setState((old) => ({ ...old, result, assessment: result.assessment, submitting: false }))
      if (onPromoted) {
        try {
          await onPromoted()
        } catch {
          // La promoción ya fue confirmada; un fallo de refresco no la revierte.
        }
      }
    } catch (error: unknown) {
      setState((old) => ({ ...old, submitting: false, message: errorMessage(error) }))
    }
  }

  const assessment = state.assessment

  return <section className="promotion-gate" aria-label="Promotion Gate">
    <div className="promotion-gate__heading">
      <div><span>Promotion Gate</span><h2>Activación gobernada de versión</h2><p>Evalúa el candidate importado y, sólo cuando el gate esté listo, archiva la ACTIVE anterior y activa la nueva versión.</p></div>
      <button onClick={() => void loadImports()} disabled={state.loading}>Actualizar</button>
    </div>

    {state.message && <div className="promotion-message" role="alert">{state.message}</div>}

    <div className="promotion-toolbar">
      <label><span>Versión importada</span><select value={state.versionId ?? ''} onChange={(event) => setState((old) => ({ ...old, versionId: event.target.value || null, assessment: null, result: null }))}><option value="">Sin versiones</option>{state.imports.map((job) => <option key={job.id} value={job.knowledge_version_id ?? ''}>{job.knowledge_version_id?.slice(0, 8) ?? 'sin versión'} · {when(job.requested_at)}</option>)}</select></label>
      {selectedImport && <code className="promotion-job">import job {selectedImport.id.slice(0, 8)}</code>}
    </div>

    {assessment && <div className="promotion-layout">
      <article className="promotion-card">
        <header><div><span>Assessment</span><h3>{assessment.knowledge_version}</h3></div><strong className={assessment.promotable ? 'is-ready' : 'is-blocked'}>{assessment.promotable ? 'Promotable' : 'Bloqueada'}</strong></header>
        <div className="promotion-metrics">
          <div><span>Modo</span><strong>{assessment.promotion_mode}</strong></div>
          <div><span>Estado</span><strong>{assessment.version_status}</strong></div>
          <div><span>ACTIVE actual</span><strong>{assessment.current_active_knowledge_version ?? 'ninguna'}</strong></div>
          <div><span>Warnings</span><strong>{assessment.build_warning_count}</strong></div>
        </div>
        {assessment.diff_totals && <div className="promotion-diff">{Object.entries(assessment.diff_totals).map(([key, value]) => <div key={key}><strong>{value}</strong><span>{key}</span></div>)}</div>}
        <div className="promotion-lineage"><span>Import</span><code>{assessment.pipeline_import_job_id ?? '—'}</code><span>Reconciliation</span><code>{assessment.source_reconciliation_job_id ?? '—'}</code><span>Removal review</span><code>{assessment.removal_review_set_id ?? '—'}</code></div>
      </article>

      <article className="promotion-card">
        <header><div><span>Gate</span><h3>Bloqueos y autorización</h3></div></header>
        {assessment.blockers.length === 0
          ? <div className="promotion-ready">No hay blockers activos.</div>
          : <ul className="promotion-blockers">{assessment.blockers.map((blocker, index) => <li key={`${blocker.code}-${index}`}><strong>{blocker.code}</strong><span>{blocker.message}</span><small>{blocker.count}{blocker.entity_type ? ` · ${blocker.entity_type}` : ''}</small></li>)}</ul>}
        {assessment.warnings.length > 0 && <div className="promotion-warnings"><strong>Warnings</strong>{assessment.warnings.map((warning, index) => <p key={`${warning}-${index}`}>{warning}</p>)}</div>}

        <div className="promotion-form">
          <label><span>Revisor</span><input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="operador / usuario" /></label>
          <label><span>Razón</span><textarea rows={4} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Motivo de la activación" /></label>
          <button onClick={() => void promote()} disabled={!assessment.promotable || !reviewerId.trim() || !reason.trim() || state.submitting}>Promover a ACTIVE</button>
        </div>
      </article>
    </div>}

    {state.result && <div className="promotion-success"><strong>Versión activada</strong><span>{state.result.knowledge_version}</span><small>Promotion {state.result.promotion_id} · ACTIVE anterior {state.result.previous_active_version_id ?? 'ninguna'}</small><div>{Object.entries(state.result.sync_jobs).map(([target, jobId]) => <code key={target}>{target}: {jobId}</code>)}</div></div>}
  </section>
}
