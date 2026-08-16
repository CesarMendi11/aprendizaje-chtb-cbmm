import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AdminApiError,
  confirmRemovalRemove,
  confirmRemovalRetain,
  createCanonicalReconciliationJob,
  dataMode,
  getPipelineJobs,
  getRemovalReview,
  getRemovalReviewHistory,
  prepareRemovalReview,
  resetRemovalDecision,
} from '../../api/client'
import type {
  PipelineJobSummary,
  RemovalReviewDecision,
  RemovalReviewHistory,
  RemovalReviewSet,
} from '../../types/admin'
import './removal-review.css'

type State = {
  imports: PipelineJobSummary[]
  candidateVersionId: string | null
  review: RemovalReviewSet | null
  selectedDecisionId: string | null
  history: RemovalReviewHistory | null
  loading: boolean
  submitting: boolean
  message: string | null
}

const errorMessage = (error: unknown) =>
  error instanceof AdminApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : 'Ocurrió un error inesperado en Removal HITL.'

const decisionLabel = (value: RemovalReviewDecision['current_decision']) => {
  if (value === 'retain_from_active') return 'Retener ACTIVE'
  if (value === 'confirmed_remove') return 'Eliminar confirmado'
  return 'Pendiente'
}

const entityLabel = (value: string) =>
  ({ ui_state: 'Estado UI', event: 'Evento', transition: 'Transición' })[value] ?? value

const when = (value: string) => new Date(value).toLocaleString()

export function RemovalReviewConsole({ onOpenJob }: { onOpenJob?: (jobId: string) => void }) {
  const [state, setState] = useState<State>({
    imports: [],
    candidateVersionId: null,
    review: null,
    selectedDecisionId: null,
    history: null,
    loading: dataMode === 'live',
    submitting: false,
    message: null,
  })
  const [reviewerId, setReviewerId] = useState('')
  const [reason, setReason] = useState('')

  const loadImports = useCallback(async () => {
    if (dataMode !== 'live') return
    setState((old) => ({ ...old, loading: true, message: null }))
    try {
      const response = await getPipelineJobs(100, 'canonical_import')
      const imports = response.items.filter(
        (job) =>
          job.status === 'succeeded' &&
          job.scope === 'full' &&
          Boolean(job.knowledge_version_id),
      )
      setState((old) => ({
        ...old,
        imports,
        candidateVersionId: old.candidateVersionId ?? imports[0]?.knowledge_version_id ?? null,
        loading: false,
      }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, loading: false, message: errorMessage(error) }))
    }
  }, [])

  const loadReview = useCallback(async (candidateVersionId: string) => {
    setState((old) => ({ ...old, loading: true, message: null, history: null }))
    try {
      const review = await getRemovalReview(candidateVersionId)
      setState((old) => ({
        ...old,
        review,
        selectedDecisionId:
          old.selectedDecisionId && review.decisions.some((item) => item.id === old.selectedDecisionId)
            ? old.selectedDecisionId
            : review.decisions[0]?.id ?? null,
        loading: false,
      }))
    } catch (error: unknown) {
      setState((old) => ({
        ...old,
        review: null,
        selectedDecisionId: null,
        loading: false,
        message: error instanceof AdminApiError && error.status === 409
          ? 'Este candidate todavía no tiene ReviewSet de removals. Puede prepararlo desde aquí.'
          : errorMessage(error),
      }))
    }
  }, [])

  useEffect(() => { void loadImports() }, [loadImports])

  useEffect(() => {
    if (!state.candidateVersionId || dataMode !== 'live') return
    void loadReview(state.candidateVersionId)
  }, [loadReview, state.candidateVersionId])

  const selectedDecision = useMemo(
    () => state.review?.decisions.find((item) => item.id === state.selectedDecisionId) ?? null,
    [state.review, state.selectedDecisionId],
  )

  const prepare = async () => {
    if (!state.candidateVersionId || state.submitting) return
    setState((old) => ({ ...old, submitting: true, message: null }))
    try {
      const review = await prepareRemovalReview(state.candidateVersionId)
      setState((old) => ({
        ...old,
        review,
        selectedDecisionId: review.decisions[0]?.id ?? null,
        history: null,
        submitting: false,
      }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, submitting: false, message: errorMessage(error) }))
    }
  }

  const act = async (action: 'retain' | 'remove' | 'reset') => {
    if (!selectedDecision || !state.review || !reviewerId.trim() || !reason.trim() || state.submitting) {
      return
    }
    setState((old) => ({ ...old, submitting: true, message: null }))
    const payload = {
      reviewer_id: reviewerId.trim(),
      reason: reason.trim(),
      expected_revision: selectedDecision.review_revision,
    }
    try {
      if (action === 'retain') await confirmRemovalRetain(selectedDecision.id, payload)
      else if (action === 'remove') await confirmRemovalRemove(selectedDecision.id, payload)
      else await resetRemovalDecision(selectedDecision.id, payload)
      setReason('')
      await loadReview(state.review.candidate_version_id)
      setState((old) => ({ ...old, submitting: false }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, submitting: false, message: errorMessage(error) }))
    }
  }

  const loadHistory = async () => {
    if (!selectedDecision) return
    setState((old) => ({ ...old, loading: true, message: null }))
    try {
      const history = await getRemovalReviewHistory(selectedDecision.id)
      setState((old) => ({ ...old, history, loading: false }))
    } catch (error: unknown) {
      setState((old) => ({ ...old, loading: false, message: errorMessage(error) }))
    }
  }

  const reconcile = async () => {
    if (!state.review || state.review.pending_review !== 0 || state.submitting) return
    setState((old) => ({ ...old, submitting: true, message: null }))
    try {
      const job = await createCanonicalReconciliationJob({
        candidate_version_id: state.review.candidate_version_id,
      })
      setState((old) => ({ ...old, submitting: false }))
      onOpenJob?.(job.id)
    } catch (error: unknown) {
      setState((old) => ({ ...old, submitting: false, message: errorMessage(error) }))
    }
  }

  return <section className="removal-review" aria-label="Removal reconciliation HITL">
    <div className="removal-review__heading">
      <div>
        <span>Removal HITL</span>
        <h2>Reconciliación humana de ausencias</h2>
        <p>Una ausencia en un candidate FULL o parcial no prueba eliminación. Confirme retención o eliminación antes de construir el candidate reconciliado final.</p>
      </div>
      <button onClick={() => void loadImports()} disabled={dataMode !== 'live' || state.loading}>Actualizar</button>
    </div>

    {dataMode !== 'live' && <div className="removal-notice">Removal HITL requiere <code>VITE_ADMIN_API_MODE=live</code>.</div>}
    {state.message && <div className="removal-message" role="alert">{state.message}</div>}

    <div className="removal-toolbar">
      <label>
        <span>Candidate importado</span>
        <select
          value={state.candidateVersionId ?? ''}
          onChange={(event) => setState((old) => ({
            ...old,
            candidateVersionId: event.target.value || null,
            review: null,
            selectedDecisionId: null,
            history: null,
          }))}
        >
          <option value="">Sin candidates</option>
          {state.imports.map((job) => <option key={job.id} value={job.knowledge_version_id ?? ''}>
            {job.knowledge_version_id?.slice(0, 8) ?? 'sin versión'} · {when(job.requested_at)}
          </option>)}
        </select>
      </label>
      {!state.review && <button className="removal-primary" onClick={() => void prepare()} disabled={!state.candidateVersionId || state.submitting}>Preparar ReviewSet</button>}
      {state.review && <button className="removal-primary" onClick={() => void reconcile()} disabled={state.review.pending_review !== 0 || state.submitting}>Crear reconciliación</button>}
    </div>

    {state.review && <>
      <div className="removal-counts">
        <div><strong>{state.review.decision_count}</strong><span>Total</span></div>
        <div><strong>{state.review.pending_review}</strong><span>Pendientes</span></div>
        <div><strong>{state.review.retain_from_active}</strong><span>Retain</span></div>
        <div><strong>{state.review.confirmed_remove}</strong><span>Remove</span></div>
      </div>

      <div className="removal-layout">
        <article className="removal-card removal-list">
          <header><div><span>Decisiones</span><h3>{state.review.candidate_knowledge_version}</h3></div><code>{state.review.candidate_origin}</code></header>
          <div className="removal-items">
            {state.review.decisions.map((decision) => <button
              key={decision.id}
              className={decision.id === selectedDecision?.id ? 'is-selected' : ''}
              onClick={() => setState((old) => ({ ...old, selectedDecisionId: decision.id, history: null }))}
            >
              <span><strong>{decision.canonical_id}</strong><small>{entityLabel(decision.entity_type)} · rev. {decision.review_revision}</small></span>
              <em className={`removal-decision removal-decision--${decision.current_decision ?? 'pending'}`}>{decisionLabel(decision.current_decision)}</em>
            </button>)}
          </div>
        </article>

        <article className="removal-card removal-detail">
          {!selectedDecision && <div className="removal-empty">Seleccione una decisión.</div>}
          {selectedDecision && <>
            <header><div><span>{entityLabel(selectedDecision.entity_type)}</span><h3>{selectedDecision.canonical_id}</h3></div><em className={`removal-decision removal-decision--${selectedDecision.current_decision ?? 'pending'}`}>{decisionLabel(selectedDecision.current_decision)}</em></header>
            <dl className="removal-meta">
              <div><dt>Screen</dt><dd>{selectedDecision.screen_id ?? 'sin screen'}</dd></div>
              <div><dt>Plan</dt><dd>{selectedDecision.plan_reason}</dd></div>
              <div><dt>Propuesta</dt><dd>{selectedDecision.proposed_decision}</dd></div>
              <div><dt>Confirmación</dt><dd>{selectedDecision.removal_confirmation ?? '—'}</dd></div>
            </dl>

            <div className="removal-form">
              <label><span>Revisor</span><input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="operador / usuario" /></label>
              <label><span>Razón</span><textarea rows={4} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Evidencia o criterio usado para decidir" /></label>
              <div className="removal-actions">
                <button className="retain" onClick={() => void act('retain')} disabled={selectedDecision.current_decision !== null || !reviewerId.trim() || !reason.trim() || state.submitting}>Retener ACTIVE</button>
                <button className="remove" onClick={() => void act('remove')} disabled={selectedDecision.current_decision !== null || !reviewerId.trim() || !reason.trim() || state.submitting}>Confirmar eliminación</button>
                <button onClick={() => void act('reset')} disabled={selectedDecision.current_decision === null || !reviewerId.trim() || !reason.trim() || state.submitting}>Volver a pendiente</button>
                <button onClick={() => void loadHistory()} disabled={state.loading}>Ver historial</button>
              </div>
            </div>

            {state.history && <div className="removal-history">
              <h4>Historial</h4>
              {state.history.actions.length === 0
                ? <p>Sin acciones registradas.</p>
                : <ol>{state.history.actions.map((action) => <li key={action.id}><strong>{action.action}</strong><span>{action.previous_decision ?? 'pending'} → {action.new_decision ?? 'pending'}</span><small>{action.reviewer_subject} · {when(action.created_at)}</small><p>{action.review_notes}</p></li>)}</ol>}
            </div>}
          </>}
        </article>
      </div>
    </>}
  </section>
}
