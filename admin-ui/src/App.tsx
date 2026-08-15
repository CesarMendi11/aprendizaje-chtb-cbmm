import { useCallback, useEffect, useMemo, useState } from 'react'
import { AdminApiError, getKnowledgeTree, getScreenReviewContext } from './api/client'
import { AdminLayout, type AdminSection } from './layout/AdminLayout'
import { OverviewPage } from './pages/OverviewPage'
import { StructuralKnowledgePage } from './pages/StructuralKnowledgePage'
import { SemanticKnowledgePage } from './pages/SemanticKnowledgePage'
import { PublicationPage } from './pages/PublicationPage'
import { PipelinePage } from './pages/PipelinePage'
import type { KnowledgeTreeResponse, ScreenReviewContextResponse } from './types/admin'

type LoadState<T> = { status: 'loading'; data?: T } | { status: 'ready'; data: T } | { status: 'error'; message: string; data?: T }
const messageOf = (error: unknown) => error instanceof AdminApiError ? error.message : 'Ocurrió un error inesperado al cargar los datos.'

export default function App() {
  const [section, setSection] = useState<AdminSection>('overview')
  const [pipelineFocusJobId, setPipelineFocusJobId] = useState<string | null>(null)
  const [tree, setTree] = useState<LoadState<KnowledgeTreeResponse>>({ status: 'loading' })
  const [detail, setDetail] = useState<LoadState<ScreenReviewContextResponse> | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const loadTree = useCallback(async () => {
    setTree((old) => ({ status: 'loading', data: old.data }))
    try { setTree({ status: 'ready', data: await getKnowledgeTree() }) }
    catch (error: unknown) { setTree((old) => ({ status: 'error', message: messageOf(error), data: old.data })) }
  }, [])

  const loadDetail = useCallback(async (id: string) => {
    setSelectedId(id)
    setDetail((old) => ({ status: 'loading', data: old?.data }))
    try { setDetail({ status: 'ready', data: await getScreenReviewContext(id) }) }
    catch (error: unknown) { setDetail((old) => ({ status: 'error', message: messageOf(error), data: old?.data })) }
  }, [])

  useEffect(() => { void loadTree() }, [loadTree])
  const erp = tree.data?.erps[0] ?? null
  const firstScreen = useMemo(() => erp?.modules.flatMap((module) => module.screens)[0]?.screen_id ?? erp?.unassigned_screens[0]?.screen_id ?? null, [erp])
  useEffect(() => { if (!selectedId && firstScreen) void loadDetail(firstScreen) }, [firstScreen, loadDetail, selectedId])

  const refreshKnowledge = useCallback(async () => {
    if (selectedId) await loadDetail(selectedId)
    await loadTree()
  }, [loadDetail, loadTree, selectedId])

  const retryDetail = useCallback(() => {
    if (selectedId) void loadDetail(selectedId)
    else void loadTree()
  }, [loadDetail, loadTree, selectedId])

  const knowledgePageProps = erp ? {
    erp,
    selectedId,
    detail,
    treeMessage: tree.status === 'error' ? tree.message : null,
    onSelect: (id: string) => void loadDetail(id),
    onRetryTree: () => void loadTree(),
    onRetryDetail: retryDetail,
    onRefresh: refreshKnowledge,
  } : null

  return <AdminLayout
    activeSection={section}
    onNavigate={setSection}
    erpName={erp?.name ?? 'CBMM'}
    knowledgeVersion={erp?.knowledge_version ?? null}
    sourceStatus={tree.status}
    onReload={() => void loadTree()}
    reloading={tree.status === 'loading'}
  >
    {section === 'overview' && <OverviewPage erp={erp} />}
    {section === 'structural' && (knowledgePageProps ? <StructuralKnowledgePage {...knowledgePageProps} onOpenJob={(jobId) => { setPipelineFocusJobId(jobId); setSection('pipeline') }} /> : <KnowledgeUnavailable state={tree} retry={loadTree} />)}
    {section === 'semantic' && (knowledgePageProps ? <SemanticKnowledgePage {...knowledgePageProps} /> : <KnowledgeUnavailable state={tree} retry={loadTree} />)}
    {section === 'publication' && <PublicationPage onOpenJob={(jobId) => { setPipelineFocusJobId(jobId); setSection('pipeline') }} />}
    {section === 'pipeline' && <PipelinePage focusJobId={pipelineFocusJobId} />}
  </AdminLayout>
}

function KnowledgeUnavailable({ state, retry }: { state: LoadState<KnowledgeTreeResponse>; retry: () => void | Promise<void> }) {
  const message = state.status === 'error' ? state.message : 'La fuente de datos todavía no contiene un ERP activo con pantallas.'
  return <section className="admin-page"><div className="center-state" role={state.status === 'error' ? 'alert' : undefined}>{state.status === 'loading' ? <span className="spinner" /> : <div className="error-icon">!</div>}<h1>{state.status === 'loading' ? 'Cargando conocimiento' : 'Conocimiento no disponible'}</h1><p>{message}</p>{state.status !== 'loading' && <button onClick={() => void retry()}>Reintentar</button>}</div></section>
}
