import { PageHeader } from '../components/PageHeader'
import { PipelineControl } from '../features/pipeline-control/PipelineControl'
import { PromotionGateConsole } from '../features/promotion-gate/PromotionGateConsole'
import './page-shell.css'

export function PublicationPage({ onOpenJob }: { onOpenJob: (jobId: string) => void }) {
  return <section className="admin-page">
    <PageHeader eyebrow="Operación" title="Publicación gobernada" description="Proyecta únicamente conocimiento autorizado desde PostgreSQL hacia Neo4j, Chroma estructural y Chroma semántico." />
    <div className="publication-principle"><span>Fuente de verdad</span><strong>PostgreSQL autoriza</strong><i>→</i><strong>Neo4j relaciona</strong><i>→</i><strong>Chroma encuentra</strong></div>
    <PromotionGateConsole />
    <PipelineControl view="publication" onOpenJob={onOpenJob} />
  </section>
}
