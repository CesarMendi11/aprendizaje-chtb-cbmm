import { useEffect, useState } from 'react'
import { AdminApiError, approveSemanticProposal, correctSemanticProposal, createSemanticInferenceJob, dataMode, getPipelineJob, rejectSemanticProposal } from '../../api/client'
import type { AdminProposalSummary, NetworkTraceEvidence, PipelineJobDetail, ScreenPurposeInference, ScreenReviewContextResponse } from '../../types/admin'
import { EmptyState } from '../../components/EmptyState'
import { StatusBadge } from '../../components/StatusBadge'

type Tab = 'summary' | 'structure' | 'inference' | 'traceability'
export type ScreenDetailMode = 'full' | 'structural' | 'semantic'
type SemanticAction = 'approve' | 'correct' | 'reject'
const allTabs: { id: Tab; label: string }[] = [{ id: 'summary', label: 'Resumen' }, { id: 'structure', label: 'Estructura' }, { id: 'inference', label: 'Inferencia' }, { id: 'traceability', label: 'Trazabilidad' }]
const tabsForMode = (mode: ScreenDetailMode) => mode === 'structural' ? allTabs.filter((item) => item.id === 'structure' || item.id === 'traceability') : mode === 'semantic' ? allTabs.filter((item) => item.id !== 'structure') : allTabs
const shortened = (value: string | null) => value ? value.length > 22 ? `${value.slice(0, 10)}…${value.slice(-8)}` : value : 'No disponible en el snapshot de demostración'
const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))
const messageOf = (error: unknown) => error instanceof AdminApiError ? error.message : error instanceof Error ? error.message : 'Ocurrió un error inesperado.'
function List({ values, empty = 'No disponible en el snapshot de demostración.' }: { values: string[]; empty?: string }) { return values.length ? <ul className="detail-list">{values.map((value) => <li key={value}>{value}</li>)}</ul> : <EmptyState>{empty}</EmptyState> }
function Metric({ label, value }: { label: string; value: string | number }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div> }

function networkTraceSummary(trace: NetworkTraceEvidence) {
  const values = [
    `Evidence: ${trace.evidence_id}`,
    `Métodos observados: ${trace.methods.length ? trace.methods.join(', ') : 'Ninguno'}`,
    `Endpoints observados: ${trace.endpoint_paths.length ? trace.endpoint_paths.join(', ') : 'Ninguno'}`,
    `Status observados: ${trace.status_codes.length ? trace.status_codes.join(', ') : 'Ninguno'}`,
    `Recursos: ${trace.resource_types.length ? trace.resource_types.join(', ') : 'Ninguno'}`,
    `Query keys: ${trace.query_keys.length ? trace.query_keys.join(', ') : 'Ninguna'}`,
    `Observaciones: ${trace.observation_count}`,
    `Endpoints únicos: ${trace.endpoint_count}`,
    `Solo lectura: ${trace.read_only ? 'Sí' : 'No · excluida del prompt semántico'}`,
  ]
  return values.join(' · ')
}

function lifecycleTrace(summary: AdminProposalSummary) {
  if (summary.lifecycle_origin === 'generated') return []
  return [
    `Origen: ${summary.lifecycle_origin}`,
    `Source proposal: ${summary.source_semantic_proposal_id ?? 'No disponible'}`,
    `Source version: ${summary.source_knowledge_version_id ?? 'No disponible'}`,
    `Source review: ${summary.source_review_status ?? 'No disponible'} · rev. ${summary.source_review_revision ?? '—'}`,
    `Source effective hash: ${shortened(summary.source_effective_content_hash)}`,
  ]
}

export function ScreenDetail({ context, onNavigate, onRefresh, mode = 'full' }: { context: ScreenReviewContextResponse; onNavigate: (id: string) => void; onRefresh: () => void | Promise<void>; mode?: ScreenDetailMode }) {
  const availableTabs = tabsForMode(mode)
  const defaultTab: Tab = mode === 'structural' ? 'structure' : 'summary'
  const [tab, setTab] = useState<Tab>(defaultTab)
  const [reviewer, setReviewer] = useState('')
  const [reason, setReason] = useState('')
  const [correctionMode, setCorrectionMode] = useState(false)
  const [correctionText, setCorrectionText] = useState('')
  const [actionBusy, setActionBusy] = useState<SemanticAction | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [inferenceJob, setInferenceJob] = useState<PipelineJobDetail | null>(null)

  const p = context.effective_payload
  const e = context.structural_evidence
  const proposal = context.active_proposal
  const proposalStatus = proposal?.summary.current_review_status ?? null
  const proposalFresh = proposal ? proposal.evidence_matches_current_structure !== false : true
  const reviewable = dataMode === 'live' && proposalStatus === 'pending_review' && proposalFresh
  const inferenceAllowed = dataMode === 'live' && ['approved', 'corrected'].includes(context.screen.structural_review_status) && context.semantic_state === 'no_proposal'

  useEffect(() => { setTab(defaultTab) }, [mode, defaultTab])

  useEffect(() => {
    setCorrectionMode(false)
    setCorrectionText(JSON.stringify(proposal?.effective_payload ?? p ?? {}, null, 2))
    setActionMessage(null)
    setActionError(null)
    setInferenceJob(null)
  }, [context.screen.screen_id, proposal?.summary.semantic_id])

  async function refreshAll() {
    await onRefresh()
  }

  async function runInference() {
    if (!inferenceAllowed) return
    setActionError(null)
    setActionMessage(null)
    try {
      let job = await createSemanticInferenceJob({ screen_id: context.screen.screen_id })
      setInferenceJob(job)
      for (let attempt = 0; attempt < 240 && ['queued', 'running'].includes(job.status); attempt += 1) {
        await sleep(1000)
        job = await getPipelineJob(job.id)
        setInferenceJob(job)
      }
      if (job.status === 'succeeded') {
        const origin = String(job.result_payload?.lifecycle_origin ?? '')
        setActionMessage(origin === 'carried_forward' ? 'Lifecycle completado: la semántica fue heredada sin llamar a Ollama.' : origin === 'reinferred' ? 'Lifecycle completado: la semántica fue reinferida y requiere HITL.' : 'Lifecycle semántico completado.')
        await refreshAll()
      } else if (job.status === 'failed') {
        setActionError(job.error_summary ?? 'La inferencia semántica fue rechazada o falló.')
      } else if (['queued', 'running'].includes(job.status)) {
        setActionError('La inferencia continúa ejecutándose. Consulte el monitor de jobs para seguirla.')
      }
    } catch (error: unknown) {
      setActionError(messageOf(error))
    }
  }

  async function performReview(action: SemanticAction) {
    if (!proposal || !reviewable) return
    const cleanReviewer = reviewer.trim()
    const cleanReason = reason.trim()
    if (!cleanReviewer || !cleanReason) {
      setActionError('Revisor y razón son obligatorios para registrar una decisión HITL.')
      return
    }
    setActionBusy(action)
    setActionError(null)
    setActionMessage(null)
    const base = {
      reviewer_id: cleanReviewer,
      reason: cleanReason,
      expected_status: proposal.summary.current_review_status,
      expected_revision: proposal.summary.review_revision,
    }
    try {
      if (action === 'approve') await approveSemanticProposal(proposal.summary.semantic_id, base)
      if (action === 'reject') await rejectSemanticProposal(proposal.summary.semantic_id, base)
      if (action === 'correct') {
        let corrected: unknown
        try { corrected = JSON.parse(correctionText) } catch { throw new Error('El JSON corregido no es válido.') }
        if (!isScreenPurposeInference(corrected)) throw new Error('El JSON corregido no cumple la forma mínima de ScreenPurposeInference.')
        await correctSemanticProposal(proposal.summary.semantic_id, { ...base, corrected_payload: corrected })
      }
      setActionMessage(action === 'approve' ? 'Propuesta semántica aprobada.' : action === 'reject' ? 'Propuesta semántica rechazada.' : 'Propuesta semántica corregida y aceptada como corrección humana.')
      setReviewer('')
      setReason('')
      setCorrectionMode(false)
      await refreshAll()
    } catch (error: unknown) {
      setActionError(messageOf(error))
    } finally {
      setActionBusy(null)
    }
  }

  return <article className="detail">
    <header className="detail-header"><div><p className="breadcrumb">{context.erp.name} <span>/</span> {context.module?.name ?? 'Sin módulo'}</p><div className="title-line"><h1>{context.screen.title ?? 'Pantalla sin título'}</h1><StatusBadge status={mode === 'structural' ? context.screen.structural_review_status : context.semantic_state} /></div><p className="route">{context.screen.route ?? 'Ruta no disponible en el snapshot de demostración'}</p></div><div className="position"><span>Posición en el módulo</span><strong>{context.navigation.module_screen_position} de {context.navigation.module_screen_total}</strong></div></header>
    <div className="state-strip"><span>Estructura <StatusBadge status={context.screen.structural_review_status} /></span><span>Semántica <StatusBadge status={context.semantic_state} /></span><span>Evidencia <b>{e.evidence_available ? 'Disponible' : 'No disponible'}</b></span></div>
    {mode === 'structural' ? <section className="metrics" aria-label="Indicadores estructurales"><Metric label="Campos" value={e.fields.length}/><Metric label="Controles" value={e.controls.length}/><Metric label="Tablas" value={e.tables.length}/><Metric label="Transiciones" value={e.transitions.length}/></section> : <section className="metrics" aria-label="Indicadores semánticos"><Metric label="Capabilities" value={p?.supported_capabilities.length ?? 0}/><Metric label="Origen lifecycle" value={proposal?.summary.lifecycle_origin ?? 'No aplica'}/><Metric label="Acciones de revisión" value={context.traceability.review_action_count}/><Metric label="Evidencia vs. estructura" value={proposal && (proposal.historical_structure_hash === null || proposal.diagnostic === 'Comparación no disponible en el snapshot de demostración.') ? 'No disponible en el snapshot de demostración' : proposal?.evidence_matches_current_structure ? 'Coincide' : proposal ? 'Difiere' : 'No aplica'}/></section>}
    <div className="tabs" role="tablist" aria-label="Detalle de pantalla">{availableTabs.map(({ id, label }) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>)}</div>
    <section className="tab-panel" role="tabpanel">
      {tab === 'summary' && <><Section title="Propósito"><p className="purpose">{p?.purpose_summary ?? 'La pantalla no tiene una propuesta semántica activa.'}</p></Section><Section title="Capabilities">{p?.supported_capabilities.length ? <div className="capabilities">{p.supported_capabilities.map((capability) => <div className="capability" key={`${capability.statement}-${capability.evidence_refs.join('|')}`}><span className="check" aria-hidden="true">✓</span><div><strong>{capability.statement}</strong><small>Evidencia: {capability.evidence_refs.join(', ')}</small></div></div>)}</div> : <EmptyState>No hay capabilities disponibles.</EmptyState>}</Section><div className="two-cols"><Section title="Limitaciones"><List values={p?.limitations ?? []}/></Section><Section title="Incertidumbres"><List values={p?.uncertainties ?? []}/></Section></div><SemanticGovernancePanel context={context} proposalFresh={proposalFresh} reviewer={reviewer} reason={reason} correctionMode={correctionMode} correctionText={correctionText} actionBusy={actionBusy} actionMessage={actionMessage} actionError={actionError} inferenceJob={inferenceJob} inferenceAllowed={inferenceAllowed} reviewable={reviewable} onReviewer={setReviewer} onReason={setReason} onCorrectionMode={setCorrectionMode} onCorrectionText={setCorrectionText} onInfer={() => void runInference()} onReview={(action) => void performReview(action)}/></>}
      {tab === 'structure' && <div className="structure-grid"><EvidenceGroup title="Campos" values={e.fields.map((x) => x.label)}/><EvidenceGroup title="Controles" values={e.controls.map((x) => x.label)}/><EvidenceGroup title="Tablas y columnas" values={e.tables.map((x) => `${x.name}: ${x.columns.map((c) => c.label).join(', ')}`)}/><EvidenceGroup title="Estados UI" values={e.ui_states.map((x) => x.title)}/><EvidenceGroup title="Eventos" values={e.events.map((x) => x.label)}/><EvidenceGroup title="Transiciones" values={e.transitions.map((x) => x.transition_id)}/><EvidenceGroup title="Network Evidence · metadata agregada" values={e.network_traces.map(networkTraceSummary)}/><EvidenceGroup title="IDs de evidencia" values={e.evidence_ids}/><EvidenceGroup title="Advertencias" values={e.warnings}/></div>}
      {tab === 'inference' && <><div className="info-grid"><Metric label="Semantic ID" value={proposal?.summary.semantic_id ?? 'No disponible'}/><Metric label="Tipo semántico" value={proposal?.summary.semantic_type ?? 'No disponible'}/><Metric label="Estado" value={proposal?.summary.current_review_status ?? 'Sin propuesta'}/><Metric label="Origen lifecycle" value={proposal?.summary.lifecycle_origin ?? 'No disponible'}/><Metric label="Modelo" value={proposal?.summary.generation_model ?? 'No disponible'}/><Metric label="Prompt version" value={proposal?.summary.prompt_version ?? 'No disponible'}/><Metric label="Evidence hash" value={shortened(proposal?.summary.evidence_hash ?? null)}/></div>{proposal && <Section title="Provenance lifecycle"><List values={lifecycleTrace(proposal.summary)} empty="Propuesta generada en esta versión; no tiene source cross-version."/></Section>}<Section title="Snapshot histórico"><p>{proposal?.evidence.evidence_available ? `${proposal.evidence.evidence_ids.length} referencias de evidencia disponibles.` : 'Evidencia histórica no disponible.'}</p></Section><Section title="Network Evidence del snapshot histórico"><List values={proposal?.evidence.network_traces.map(networkTraceSummary) ?? []} empty="La propuesta no contiene Network Evidence segura en su snapshot."/></Section><Section title="Comparación con la estructura actual"><p>{proposal && (proposal.historical_structure_hash === null || proposal.diagnostic === 'Comparación no disponible en el snapshot de demostración.') ? 'No disponible en el snapshot de demostración' : proposal?.evidence_matches_current_structure ? 'La evidencia histórica coincide con la estructura actual.' : proposal ? 'La evidencia histórica difiere de la estructura actual.' : 'No existe una propuesta para comparar.'}</p></Section>{proposal && <Section title="Payload semántico efectivo"><pre className="semantic-json">{JSON.stringify(proposal.effective_payload, null, 2)}</pre></Section>}</>}
      {tab === 'traceability' && <><div className="info-grid"><Metric label="Propuestas" value={context.traceability.proposal_count}/><Metric label="Acciones de revisión" value={context.traceability.review_action_count}/><Metric label="Identidad verificada" value={context.reviewer_identity_verified ? 'Sí' : 'No'}/></div><Section title="IDs de evidencia"><List values={context.traceability.evidence_ids}/></Section><Section title="Network Evidence actual"><List values={e.network_traces.map(networkTraceSummary)} empty="No existe Network Evidence segura para la estructura actual."/></Section>{proposal && <Section title="Provenance lifecycle"><List values={lifecycleTrace(proposal.summary)} empty="Propuesta generada en esta versión; no tiene source cross-version."/></Section>}<Section title="Historial">{context.review_history.length ? <List values={context.review_history.map((item) => `${item.action}: ${item.previous_status} → ${item.new_status} · ${item.reviewer_id}`)}/> : <EmptyState>No existen acciones de revisión registradas.</EmptyState>}</Section><Section title="Advertencias"><List values={context.traceability.warnings} empty="No existen advertencias."/></Section><div className="navigation"><button disabled={!context.navigation.previous_screen_id} onClick={() => context.navigation.previous_screen_id && onNavigate(context.navigation.previous_screen_id)}>← Pantalla anterior</button><button disabled={!context.navigation.next_screen_id} onClick={() => context.navigation.next_screen_id && onNavigate(context.navigation.next_screen_id)}>Pantalla siguiente →</button></div></>}
    </section>
  </article>
}

function SemanticGovernancePanel({ context, proposalFresh, reviewer, reason, correctionMode, correctionText, actionBusy, actionMessage, actionError, inferenceJob, inferenceAllowed, reviewable, onReviewer, onReason, onCorrectionMode, onCorrectionText, onInfer, onReview }: {
  context: ScreenReviewContextResponse
  proposalFresh: boolean
  reviewer: string
  reason: string
  correctionMode: boolean
  correctionText: string
  actionBusy: SemanticAction | null
  actionMessage: string | null
  actionError: string | null
  inferenceJob: PipelineJobDetail | null
  inferenceAllowed: boolean
  reviewable: boolean
  onReviewer: (value: string) => void
  onReason: (value: string) => void
  onCorrectionMode: (value: boolean) => void
  onCorrectionText: (value: string) => void
  onInfer: () => void
  onReview: (action: SemanticAction) => void
}) {
  const proposal = context.active_proposal
  const inferenceRunning = inferenceJob && ['queued', 'running'].includes(inferenceJob.status)
  return <section className="semantic-governance" aria-label="Gobierno semántico">
    <div className="semantic-governance__head"><div><p className="eyebrow">HITL SEMÁNTICO</p><h2>Inferencia y revisión humana</h2><p>El executor decide determinísticamente entre generar, heredar o reinferir; solo una propuesta nueva pendiente requiere una decisión HITL local.</p></div><div className="semantic-governance__status"><span>Identidad verificada</span><strong>No · RBAC pendiente</strong></div></div>
    {!proposal && <div className="semantic-inference-launch"><div><strong>Sin propuesta semántica</strong><p>{inferenceAllowed ? 'La estructura está aprobada. El lifecycle decidirá carry-forward, reinferencia o generación sobre evidencia segura.' : 'El lifecycle requiere estructura aprobada/corregida, versión activa y modo live.'}</p></div><button className="semantic-button semantic-button--primary" disabled={!inferenceAllowed || Boolean(inferenceRunning)} onClick={onInfer}>{inferenceRunning ? 'Ejecutando…' : 'Ejecutar lifecycle semántico'}</button></div>}
    {inferenceJob && <div className={`semantic-job semantic-job--${inferenceJob.status}`}><strong>{stageLabel(inferenceJob.stage)}</strong><span>{inferenceJob.progress_percent === null ? inferenceJob.status : `${Math.round(inferenceJob.progress_percent)} %`}</span>{inferenceJob.error_summary && <small>{inferenceJob.error_summary}</small>}</div>}
    {proposal && <>
      <div className="semantic-proposal-summary"><div><span>Estado</span><StatusBadge status={proposal.summary.current_review_status}/></div><div><span>Origen</span><strong>{proposal.summary.lifecycle_origin}</strong></div><div><span>Modelo</span><strong>{proposal.summary.generation_model}</strong></div><div><span>Revisión</span><strong>{proposal.summary.review_revision}</strong></div></div>
      {!proposalFresh && <div className="semantic-alert semantic-alert--warning">La proyección Safe Evidence comparable cambió. La UI bloquea aprobar/corregir y el backend vuelve a validar la evidencia exacta; la remediación debe ocurrir mediante un nuevo ciclo de conocimiento gobernado.</div>}
      {proposal.summary.current_review_status === 'pending_review' && <div className="semantic-review-form">
        <label><span>Revisor local</span><input value={reviewer} maxLength={240} placeholder="operador-demo" onChange={(event) => onReviewer(event.target.value)}/></label>
        <label><span>Razón / notas de revisión</span><textarea value={reason} maxLength={4000} rows={3} placeholder="Verificado contra la evidencia estructural disponible." onChange={(event) => onReason(event.target.value)}/></label>
        <label className="semantic-correction-toggle"><input type="checkbox" checked={correctionMode} onChange={(event) => onCorrectionMode(event.target.checked)}/><span>Corregir el payload antes de decidir</span></label>
        {correctionMode && <label><span>Payload corregido · JSON</span><textarea className="semantic-editor" value={correctionText} rows={15} spellCheck={false} onChange={(event) => onCorrectionText(event.target.value)}/><small>El backend vuelve a validar screen_id, semantic_type, evidence_refs y grounding. La corrección humana no evita las guardas deterministas.</small></label>}
        <div className="semantic-actions"><button className="semantic-button semantic-button--approve" disabled={!reviewable || Boolean(actionBusy) || correctionMode} onClick={() => onReview('approve')}>{actionBusy === 'approve' ? 'Aprobando…' : 'Aprobar'}</button><button className="semantic-button semantic-button--correct" disabled={!reviewable || Boolean(actionBusy) || !correctionMode} onClick={() => onReview('correct')}>{actionBusy === 'correct' ? 'Corrigiendo…' : 'Guardar corrección'}</button><button className="semantic-button semantic-button--reject" disabled={!reviewable || Boolean(actionBusy) || correctionMode} onClick={() => onReview('reject')}>{actionBusy === 'reject' ? 'Rechazando…' : 'Rechazar'}</button></div>
      </div>}
      {proposal.summary.current_review_status !== 'pending_review' && proposal.summary.lifecycle_origin === 'carried_forward' && <div className="semantic-alert semantic-alert--success">Semántica publicable heredada por carry-forward desde una propuesta humana gobernada. No se creó una acción HITL local ficticia.</div>}
      {proposal.summary.current_review_status !== 'pending_review' && proposal.summary.lifecycle_origin !== 'carried_forward' && <div className="semantic-alert semantic-alert--success">La propuesta ya tiene una decisión humana local: <strong>{proposal.summary.current_review_status}</strong>. La trazabilidad se conserva en el historial.</div>}
    </>}
    {actionMessage && <div className="semantic-alert semantic-alert--success" role="status">{actionMessage}</div>}
    {actionError && <div className="semantic-alert semantic-alert--error" role="alert">{actionError}</div>}
  </section>
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    queued: 'En cola', validating_active_screen: 'Verificando pantalla activa', evidence_prepared: 'Evidencia preparada', proposal_reused: 'Reutilizando propuesta existente', carrying_forward_semantic_proposal: 'Heredando semántica compatible', generating_semantic_proposal: 'Generando o reinfiriendo con Ollama', semantic_lifecycle_blocked: 'Lifecycle bloqueado', semantic_eligibility_rejected: 'Elegibilidad semántica rechazada', semantic_generation_rejected: 'Generación rechazada', proposal_ready: 'Propuesta lista', completed: 'Completado', failed: 'Falló',
  }
  return labels[stage] ?? stage.replaceAll('_', ' ')
}

function isScreenPurposeInference(value: unknown): value is ScreenPurposeInference {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return record.semantic_type === 'screen_purpose' && typeof record.screen_id === 'string' && typeof record.purpose_summary === 'string' && Array.isArray(record.supported_capabilities) && Array.isArray(record.limitations) && Array.isArray(record.uncertainties)
}
function Section({ title, children }: { title: string; children: React.ReactNode }) { return <section className="section"><h2>{title}</h2>{children}</section> }
function EvidenceGroup({ title, values }: { title: string; values: string[] }) { return <section className="evidence-group"><h2>{title}<span>{values.length}</span></h2><List values={values}/></section> }
