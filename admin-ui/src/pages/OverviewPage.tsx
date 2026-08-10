import { SystemDashboard } from '../features/system-dashboard/SystemDashboard'
import type { KnowledgeTreeErp } from '../types/admin'
import { PageHeader } from '../components/PageHeader'
import './page-shell.css'

function SummaryMetric({ label, value, note }: { label: string; value: string | number; note: string }) {
  return <article className="overview-metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>
}

export function OverviewPage({ erp }: { erp: KnowledgeTreeErp | null }) {
  const counters = erp?.counters
  return <section className="admin-page">
    <PageHeader eyebrow="Vista general" title="Resumen del prototipo" description="Estado operativo, versión activa y situación del conocimiento gobernado en una sola vista." />
    {erp && <div className="overview-strip" aria-label="Resumen del ERP activo">
      <SummaryMetric label="Módulos" value={erp.modules.length} note="Jerarquía funcional descubierta" />
      <SummaryMetric label="Pantallas" value={counters?.total_screens ?? 0} note="Pantallas en la versión activa" />
      <SummaryMetric label="Semántica aprobada" value={(counters?.approved ?? 0) + (counters?.corrected ?? 0)} note="Pantallas con conocimiento publicable" />
      <SummaryMetric label="Pendientes semánticos" value={counters?.pending_review ?? 0} note="Propuestas que requieren HITL" />
      <SummaryMetric label="Sin propuesta" value={counters?.no_proposal ?? 0} note="Pantallas aún sin enriquecimiento" />
    </div>}
    <SystemDashboard />
    <div className="overview-note"><strong>Lectura recomendada</strong><p>PostgreSQL conserva la autoridad del conocimiento. Neo4j y ChromaDB son proyecciones recuperables y sólo deben publicar contenido autorizado.</p></div>
  </section>
}
