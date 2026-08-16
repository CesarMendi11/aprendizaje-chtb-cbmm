import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AdminApiError,
  approveStructuralReviewItem,
  dataMode,
  getPipelineJobs,
  getStructuralReviewItem,
  getStructuralReviewPackages,
} from '../../api/client'
import type {
  PipelineJobSummary,
  StructuralReviewChange,
  StructuralReviewItemDetail,
  StructuralReviewPackagesResponse,
  StructuralScreenReviewPackage,
} from '../../types/admin'
import './structural-review.css'

const mandatory = (change: StructuralReviewChange) => change.change_type === 'new' || change.change_type === 'modified'
const entityLabel = (type: string) => ({ screen: 'Pantalla', ui_state: 'Estado UI', field: 'Campo', control: 'Control', table: 'Tabla', table_column: 'Columna', link: 'Enlace', event: 'Evento', transition: 'Transición', evidence: 'Evidencia' }[type] ?? type.replaceAll('_', ' '))
const changeLabel = (type: string) => ({ new: 'Nuevo', modified: 'Modificado', removed: 'Ausente', unchanged: 'Sin cambio' }[type] ?? type)
const errorMessage = (error: unknown) => error instanceof AdminApiError ? error.message : error instanceof Error ? error.message : 'No fue posible cargar los paquetes de revisión.'
const when = (value: string) => new Date(value).toLocaleString()

interface SelectedGroup {
  key: string
  title: string
  route: string | null
  changes: StructuralReviewChange[]
  package?: StructuralScreenReviewPackage
}

export function StructuralReviewPackagesConsole() {
  const [versions, setVersions] = useState<PipelineJobSummary[]>([])
  const [versionId, setVersionId] = useState<string | null>(null)
  const [packages, setPackages] = useState<StructuralReviewPackagesResponse | null>(null)
  const [selectedGroup, setSelectedGroup] = useState<SelectedGroup | null>(null)
  const [selectedChange, setSelectedChange] = useState<StructuralReviewChange | null>(null)
  const [activeDetail, setActiveDetail] = useState<StructuralReviewItemDetail | null>(null)
  const [candidateDetail, setCandidateDetail] = useState<StructuralReviewItemDetail | null>(null)
  const [reviewerId, setReviewerId] = useState('')
  const [reason, setReason] = useState('Revisión agrupada del Screen Review Package.')
  const [loading, setLoading] = useState(dataMode === 'live')
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const loadVersions = useCallback(async () => {
    if (dataMode !== 'live') return
    setLoading(true)
    setMessage(null)
    try {
      const response = await getPipelineJobs(50, 'canonical_import')
      const found = response.items.filter((job) => job.status === 'succeeded' && Boolean(job.knowledge_version_id))
      setVersions(found)
      setVersionId((old) => old ?? found[0]?.knowledge_version_id ?? null)
    } catch (error: unknown) {
      setMessage(errorMessage(error))
      setLoading(false)
    }
  }, [])

  const loadPackages = useCallback(async (candidateVersionId: string) => {
    setLoading(true)
    setMessage(null)
    try {
      const response = await getStructuralReviewPackages(candidateVersionId)
      setPackages(response)
      setSelectedGroup(null)
      setSelectedChange(null)
      setActiveDetail(null)
      setCandidateDetail(null)
    } catch (error: unknown) {
      setPackages(null)
      setMessage(errorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadVersions() }, [loadVersions])
  useEffect(() => { if (versionId) void loadPackages(versionId) }, [loadPackages, versionId])

  const selectGroup = (group: SelectedGroup) => {
    setSelectedGroup(group)
    const first = group.changes.find(mandatory) ?? group.changes[0] ?? null
    setSelectedChange(first)
    setActiveDetail(null)
    setCandidateDetail(null)
  }

  useEffect(() => {
    if (!selectedChange) return
    let cancelled = false
    const load = async () => {
      setLoadingDetail(true)
      setMessage(null)
      try {
        const [active, candidate] = await Promise.all([
          selectedChange.active_item_id ? getStructuralReviewItem(selectedChange.active_item_id) : Promise.resolve(null),
          selectedChange.candidate_item_id ? getStructuralReviewItem(selectedChange.candidate_item_id) : Promise.resolve(null),
        ])
        if (!cancelled) { setActiveDetail(active); setCandidateDetail(candidate) }
      } catch (error: unknown) {
        if (!cancelled) setMessage(errorMessage(error))
      } finally {
        if (!cancelled) setLoadingDetail(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [selectedChange])

  const mandatoryChanges = useMemo(() => selectedGroup?.changes.filter(mandatory) ?? [], [selectedGroup])
  const pendingMandatory = mandatoryChanges.filter((change) => change.candidate_item_id)

  const approveOne = async () => {
    if (!candidateDetail || !reviewerId.trim() || submitting) return
    if (candidateDetail.current_review_status === 'approved' || candidateDetail.current_review_status === 'corrected') return
    if (candidateDetail.current_review_status !== 'pending_review') {
      setMessage(`El elemento está ${candidateDetail.current_review_status}; resuélvalo desde la cola individual.`)
      return
    }
    setSubmitting(true)
    setMessage(null)
    try {
      const updated = await approveStructuralReviewItem(candidateDetail.id, {
        reviewer_id: reviewerId.trim(),
        reason: reason.trim() || null,
        expected_status: candidateDetail.current_review_status,
        expected_revision: candidateDetail.review_revision,
      })
      setCandidateDetail(updated)
      if (versionId) await loadPackages(versionId)
    } catch (error: unknown) {
      setMessage(errorMessage(error))
    } finally { setSubmitting(false) }
  }

  const approveGroup = async () => {
    if (!selectedGroup || !reviewerId.trim() || !reason.trim() || submitting) return
    setSubmitting(true)
    setMessage(null)
    let completed = 0
    let skipped = 0
    try {
      for (const change of pendingMandatory) {
        if (!change.candidate_item_id) continue
        const detail = await getStructuralReviewItem(change.candidate_item_id)
        if (detail.current_review_status === 'approved' || detail.current_review_status === 'corrected') { skipped += 1; continue }
        if (detail.current_review_status !== 'pending_review') throw new Error(`${change.canonical_id} está ${detail.current_review_status}; requiere revisión individual.`)
        await approveStructuralReviewItem(detail.id, {
          reviewer_id: reviewerId.trim(),
          reason: reason.trim(),
          expected_status: detail.current_review_status,
          expected_revision: detail.review_revision,
        })
        completed += 1
        setProgress(`${completed + skipped} / ${pendingMandatory.length}`)
      }
      setProgress(null)
      setMessage(`Paquete revisado: ${completed} aprobados y ${skipped} ya resueltos.`)
      if (versionId) await loadPackages(versionId)
    } catch (error: unknown) {
      setMessage(`Revisión parcial (${completed} aprobados). ${errorMessage(error)}`)
    } finally { setSubmitting(false); setProgress(null) }
  }

  const selectedVersion = versions.find((job) => job.knowledge_version_id === versionId) ?? null
  const unscopedMandatory = packages?.unscoped_changes.filter(mandatory) ?? []

  return <section className="structural-review structural-packages" aria-label="Paquetes de revisión estructural">
    <div className="structural-review__heading">
      <div><span className="structural-eyebrow">Screen Review Package</span><h2>Cambios obligatorios del candidate</h2><p>Agrupa únicamente diferencias gobernadas del candidate frente a la ACTIVE. Los elementos UNCHANGED no se incluyen en las acciones de este flujo.</p></div>
      <button onClick={() => versionId ? void loadPackages(versionId) : void loadVersions()} disabled={loading}>Actualizar</button>
    </div>
    {message && <div className="structural-error" role="status">{message}</div>}
    <div className="structural-toolbar structural-toolbar--packages">
      <label><span>Versión candidate</span><select value={versionId ?? ''} onChange={(event) => setVersionId(event.target.value || null)} disabled={loading}><option value="">Sin importaciones</option>{versions.map((job) => <option key={job.id} value={job.knowledge_version_id ?? ''}>{job.scope} · {when(job.requested_at)}</option>)}</select></label>
      <div className="structural-package-version"><span>Knowledge version</span><strong>{packages?.candidate_knowledge_version ?? selectedVersion?.knowledge_version_id?.slice(0, 8) ?? '—'}</strong></div>
    </div>
    {packages && <>
      <div className="structural-counts structural-counts--packages">
        <Count label="NEW" value={packages.diff_totals.new ?? 0} />
        <Count label="MODIFIED" value={packages.diff_totals.modified ?? 0} />
        <Count label="REMOVED" value={packages.diff_totals.removed ?? 0} tone={packages.diff_totals.removed ? 'rejected' : 'approved'} />
        <Count label="Pantallas con cambios" value={packages.screens_with_changes} />
        <Count label="Sin pantalla" value={unscopedMandatory.length} />
      </div>
      <div className="structural-package-layout">
        <article className="structural-list-card">
          <div className="structural-card-head"><div><span>Paquetes</span><h3>{packages.screens_with_changes} pantallas con cambios</h3></div></div>
          <div className="structural-package-list">
            {packages.packages.map((pkg) => {
              const changes = pkg.changes.filter(mandatory)
              return <button key={pkg.screen_id} className={selectedGroup?.key === pkg.screen_id ? 'is-selected' : ''} onClick={() => selectGroup({ key: pkg.screen_id, title: pkg.title ?? pkg.screen_id, route: pkg.route, changes: pkg.changes, package: pkg })}>
                <span><strong>{pkg.title ?? pkg.screen_id}</strong><small>{pkg.route ?? pkg.screen_id}</small></span>
                <span className="structural-package-badges"><b>+{pkg.counts.new ?? 0}</b><b>~{pkg.counts.modified ?? 0}</b><em>{changes.length}</em></span>
              </button>
            })}
            {unscopedMandatory.length > 0 && <button className={selectedGroup?.key === '__unscoped__' ? 'is-selected' : ''} onClick={() => selectGroup({ key: '__unscoped__', title: 'Cambios sin pantalla resoluble', route: null, changes: packages.unscoped_changes })}><span><strong>Cambios sin pantalla</strong><small>Requieren revisión explícita fuera de un Screen Package.</small></span><span className="structural-package-badges"><em>{unscopedMandatory.length}</em></span></button>}
          </div>
        </article>
        <article className="structural-detail-card">
          {!selectedGroup && <div className="structural-empty structural-empty--detail"><strong>Seleccione una pantalla</strong><span>Verá sólo NEW/MODIFIED y podrá comparar ACTIVE contra candidate.</span></div>}
          {selectedGroup && <>
            <div className="structural-card-head"><div><span>Paquete seleccionado</span><h3>{selectedGroup.title}</h3><code>{selectedGroup.route ?? selectedGroup.key}</code></div><span className="structural-version">{mandatoryChanges.length} cambios</span></div>
            <div className="structural-package-changes">
              {mandatoryChanges.map((change) => <button key={`${change.entity_type}:${change.canonical_id}`} className={selectedChange?.canonical_id === change.canonical_id && selectedChange.entity_type === change.entity_type ? 'is-selected' : ''} onClick={() => setSelectedChange(change)}><span><strong>{changeLabel(change.change_type)}</strong> · {entityLabel(change.entity_type)}</span><code>{change.canonical_id}</code></button>)}
            </div>
            {loadingDetail && <div className="structural-empty"><span className="spinner" /> Cargando comparación…</div>}
            {!loadingDetail && selectedChange && <div className="structural-compare">
              <details open><summary>ACTIVE observado</summary><pre className="structural-json">{activeDetail ? JSON.stringify(activeDetail.source_payload, null, 2) : 'No existe en ACTIVE (NEW).'}</pre></details>
              <details open><summary>Candidate observado</summary><pre className="structural-json">{candidateDetail ? JSON.stringify(candidateDetail.source_payload, null, 2) : 'No existe en candidate.'}</pre></details>
            </div>}
            <div className="structural-package-review">
              <label><span>Revisor local</span><input value={reviewerId} onChange={(event) => setReviewerId(event.target.value)} placeholder="runtime-e2e-certification" /></label>
              <label><span>Razón de la decisión agrupada</span><textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} /></label>
              <div className="structural-review-actions">
                <button className="approve" onClick={() => void approveOne()} disabled={!candidateDetail || !mandatory(selectedChange!) || !reviewerId.trim() || submitting || candidateDetail.current_review_status === 'approved' || candidateDetail.current_review_status === 'corrected'}>Aprobar este cambio</button>
                <button className="approve" onClick={() => void approveGroup()} disabled={!reviewerId.trim() || !reason.trim() || mandatoryChanges.length === 0 || submitting || selectedGroup.changes.some((change) => change.change_type === 'removed')}>{progress ? `Aprobando ${progress}` : 'Aprobar NEW/MODIFIED de esta pantalla'}</button>
              </div>
              {candidateDetail && <small>Estado seleccionado: <b>{candidateDetail.current_review_status}</b>. Correcciones o rechazos se realizan desde la cola individual.</small>}
            </div>
          </>}
        </article>
      </div>
    </>}
    {!packages && !loading && !message && <div className="structural-empty">No hay candidate seleccionado.</div>}
  </section>
}

function Count({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return <div className={`structural-count ${tone ? `structural-count--${tone}` : ''}`}><strong>{value}</strong><span>{label}</span></div>
}
