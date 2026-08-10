import { PageHeader } from '../components/PageHeader'
import { KnowledgeWorkspace } from '../components/KnowledgeWorkspace'
import type { KnowledgeTreeErp, ScreenReviewContextResponse } from '../types/admin'
import './page-shell.css'

type DetailState =
  | { status: 'loading'; data?: ScreenReviewContextResponse }
  | { status: 'ready'; data: ScreenReviewContextResponse }
  | { status: 'error'; message: string; data?: ScreenReviewContextResponse }
  | null

export function SemanticKnowledgePage({ erp, selectedId, detail, treeMessage, onSelect, onRetryTree, onRetryDetail, onRefresh }: {
  erp: KnowledgeTreeErp
  selectedId: string | null
  detail: DetailState
  treeMessage?: string | null
  onSelect: (id: string) => void
  onRetryTree: () => void
  onRetryDetail: () => void
  onRefresh: () => void | Promise<void>
}) {
  return <section className="admin-page admin-page--knowledge">
    <PageHeader eyebrow="Conocimiento" title="Conocimiento semántico" description="Genera inferencias grounded, revisa la evidencia y registra decisiones humanas antes de publicar significado funcional." />
    <KnowledgeWorkspace erp={erp} selectedId={selectedId} detail={detail} mode="semantic" treeMessage={treeMessage} onSelect={onSelect} onRetryTree={onRetryTree} onRetryDetail={onRetryDetail} onRefresh={onRefresh} />
  </section>
}
