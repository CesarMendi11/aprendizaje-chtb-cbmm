import type { ScreenSemanticState } from '../types/admin'

const labels: Record<ScreenSemanticState, string> = { no_proposal: 'Sin propuesta', pending_review: 'Pendiente', approved: 'Aprobada', corrected: 'Corregida', rejected: 'Rechazada', mixed: 'Mixta', unavailable: 'No disponible' }
export function StatusBadge({ status }: { status: ScreenSemanticState }) { return <span className={`status status--${status}`}>{labels[status]}</span> }
