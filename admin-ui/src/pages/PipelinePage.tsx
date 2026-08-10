import { PageHeader } from '../components/PageHeader'
import { PipelineControl } from '../features/pipeline-control/PipelineControl'
import './page-shell.css'

export function PipelinePage({ focusJobId }: { focusJobId?: string | null }) {
  return <section className="admin-page">
    <PageHeader eyebrow="Operación" title="Pipeline y ejecuciones" description="Ejecuta el crawl dirigido o completo, construye canonical, importa staging y consulta la trazabilidad persistente de los jobs." />
    <PipelineControl view="pipeline" focusJobId={focusJobId} />
  </section>
}
