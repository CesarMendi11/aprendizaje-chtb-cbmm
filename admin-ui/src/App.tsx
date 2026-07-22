import { useCallback, useEffect, useMemo, useState } from 'react'
import { AdminApiError, dataMode, getKnowledgeTree, getScreenReviewContext } from './api/client'
import { KnowledgeSidebar } from './features/knowledge-tree/KnowledgeSidebar'
import { ScreenDetail } from './features/screen-review/ScreenDetail'
import type { KnowledgeTreeResponse, ScreenReviewContextResponse } from './types/admin'

type LoadState<T> = { status: 'loading'; data?: T } | { status: 'ready'; data: T } | { status: 'error'; message: string; data?: T }
const messageOf = (error: unknown) => error instanceof AdminApiError ? error.message : 'Ocurrió un error inesperado al cargar los datos.'

export default function App() {
  const [tree, setTree] = useState<LoadState<KnowledgeTreeResponse>>({ status: 'loading' })
  const [detail, setDetail] = useState<LoadState<ScreenReviewContextResponse> | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const loadTree = useCallback(async () => { setTree((old) => ({ status: 'loading', data: old.data })); try { setTree({ status: 'ready', data: await getKnowledgeTree() }) } catch (error: unknown) { setTree((old) => ({ status: 'error', message: messageOf(error), data: old.data })) } }, [])
  const loadDetail = useCallback(async (id: string) => { setSelectedId(id); setDetail((old) => ({ status: 'loading', data: old?.data })); try { setDetail({ status: 'ready', data: await getScreenReviewContext(id) }) } catch (error: unknown) { setDetail((old) => ({ status: 'error', message: messageOf(error), data: old?.data })) } }, [])
  useEffect(() => { void loadTree() }, [loadTree])
  const firstScreen = useMemo(() => { const erp = tree.data?.erps[0]; return erp?.modules.flatMap((m) => m.screens)[0]?.screen_id ?? erp?.unassigned_screens[0]?.screen_id ?? null }, [tree.data])
  useEffect(() => { if (!selectedId && firstScreen) void loadDetail(firstScreen) }, [firstScreen, loadDetail, selectedId])
  const erp = tree.data?.erps[0]
  return <div className="app-shell">
    <header className="topbar"><div className="brand"><div className="brand-mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M7 7h18v18H7zM11 12h10M11 16h10M11 20h6"/></svg></div><div><strong>Consola de conocimiento</strong><span>{erp?.name ?? 'CBMM'}</span></div></div><div className="top-actions"><span className="provisional">Administración local provisional</span><span className={`mode mode--${dataMode}`}>{dataMode === 'demo' ? 'Modo demostración' : 'Modo live'}</span><span className="source"><i />{dataMode === 'demo' ? 'Snapshot validado' : tree.status === 'ready' ? 'API disponible' : tree.status === 'error' ? 'API no disponible' : 'Verificando API'}</span><button className="reload" onClick={() => void loadTree()} disabled={tree.status === 'loading'}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2 5M20 5v6h-6"/></svg>Volver a cargar</button></div></header>
    {tree.status === 'loading' && !tree.data && <main className="center-state" aria-live="polite"><span className="spinner"/>Cargando jerarquía administrativa…</main>}
    {tree.status === 'error' && !tree.data && <ErrorState message={tree.message} retry={loadTree}/>} 
    {tree.data && !erp && <main className="center-state"><h1>Árbol de conocimiento vacío</h1><p>La fuente de datos no contiene un ERP activo con pantallas.</p><button onClick={() => void loadTree()}>Reintentar</button></main>}
    {erp && <div className="workspace"><KnowledgeSidebar erp={erp} selectedId={selectedId} onSelect={(id) => void loadDetail(id)}/><main className="main-content">{tree.status === 'error' && <div className="inline-error" role="alert">{tree.message} <button onClick={() => void loadTree()}>Reintentar árbol</button></div>}{detail?.status === 'loading' && !detail.data && <div className="detail-loading" aria-live="polite"><span className="spinner"/>Cargando detalle de pantalla…</div>}{detail?.status === 'error' && !detail.data && <ErrorState message={detail.message} retry={() => selectedId ? loadDetail(selectedId) : loadTree()}/>} {detail?.data && <><ScreenDetail context={detail.data} onNavigate={(id) => void loadDetail(id)}/>{detail.status === 'error' && <div className="detail-overlay-error" role="alert">No se pudo actualizar el detalle: {detail.message} <button onClick={() => selectedId && void loadDetail(selectedId)}>Reintentar</button></div>}</>}{!detail && <div className="detail-loading">Seleccione una pantalla para consultar su contexto.</div>}</main></div>}
  </div>
}
function ErrorState({ message, retry }: { message: string; retry: () => void | Promise<void> }) { return <main className="center-state" role="alert"><div className="error-icon">!</div><h1>No se pudieron cargar los datos</h1><p>{message}</p><button onClick={() => void retry()}>Reintentar</button></main> }
