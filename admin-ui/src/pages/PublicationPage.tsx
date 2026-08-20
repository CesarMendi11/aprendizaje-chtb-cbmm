import { useCallback, useState } from 'react'
import { PageHeader } from '../components/PageHeader'
import { PipelineControl } from '../features/pipeline-control/PipelineControl'
import { PromotionGateConsole } from '../features/promotion-gate/PromotionGateConsole'
import { StructuralPublicationReviewConsole } from '../features/structural-publication-review/StructuralPublicationReviewConsole'
import './page-shell.css'

type Props = {
  onOpenJob: (jobId: string) => void
  activeKnowledgeVersionId: string | null
  activeKnowledgeVersion: string | null
  onKnowledgeChanged: () => void | Promise<void>
}

export function PublicationPage({
  onOpenJob,
  activeKnowledgeVersionId,
  activeKnowledgeVersion,
  onKnowledgeChanged,
}: Props) {
  const [refreshToken, setRefreshToken] = useState(0)
  const handleKnowledgeChanged = useCallback(async () => {
    setRefreshToken((value) => value + 1)
    await onKnowledgeChanged()
  }, [onKnowledgeChanged])

  return <section className="admin-page">
    <PageHeader eyebrow="Operación" title="Publicación gobernada" description="Promueve versiones, cierra la cobertura estructural de la ACTIVE y proyecta únicamente conocimiento autorizado desde PostgreSQL hacia Neo4j y Chroma." />
    <div className="publication-principle"><span>Fuente de verdad</span><strong>PostgreSQL autoriza</strong><i>→</i><strong>Neo4j relaciona</strong><i>→</i><strong>Chroma encuentra</strong></div>
    <PromotionGateConsole onPromoted={handleKnowledgeChanged} />
    <StructuralPublicationReviewConsole
      knowledgeVersionId={activeKnowledgeVersionId}
      knowledgeVersion={activeKnowledgeVersion}
      onChanged={handleKnowledgeChanged}
    />
    <PipelineControl view="publication" onOpenJob={onOpenJob} refreshToken={refreshToken} />
  </section>
}
