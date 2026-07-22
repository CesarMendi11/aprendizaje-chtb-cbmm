import type {
  KnowledgeTreeResponse,
  ScreenReviewContextResponse,
} from '../types/admin'

const counters = {
  total_screens: 1,
  no_proposal: 0,
  pending_review: 1,
  approved: 0,
  corrected: 0,
  rejected: 0,
  unavailable: 0,
  warnings_total: 0,
}

const screenId = 'screen:admin-cuentasxcobrar-retenciones'
const versionId = 'demo-snapshot-verified-repository'
const structureHash = 'no-disponible-en-snapshot-demo'

const payload = {
  semantic_type: 'screen_purpose' as const,
  screen_id: screenId,
  purpose_summary: 'Permite buscar y consultar retenciones.',
  supported_capabilities: [
    {
      statement: 'Permite buscar retenciones.',
      evidence_refs: ['control:search'],
    },
    {
      statement: 'Permite visualizar retenciones.',
      evidence_refs: ['table:results'],
    },
  ],
  limitations: [],
  uncertainties: [],
}

const commonEvidence = {
  screen_id: screenId,
  screen_title: 'Retenciones',
  screen_route: null,
  module: {
    module_id: 'module:cuentasxcobrar',
    name: 'Cuentas por cobrar',
  },
  fields: [],
  controls: [],
  tables: [],
  ui_states: [],
  events: [],
  transitions: [],
  evidence_ids: [],
}

const proposal = {
  summary: {
    semantic_id: 'semantic:retenciones-purpose',
    semantic_type: 'screen_purpose',
    current_review_status: 'pending_review' as const,
    review_revision: 0,
    erp_id: 'erp:cbmm',
    knowledge_version_id: versionId,
    screen_id: screenId,
    subject_title: 'Retenciones',
    purpose_summary: payload.purpose_summary,
    generation_model: 'No disponible en el snapshot de demostración',
    prompt_version: 'No disponible en el snapshot de demostración',
    evidence_hash: 'no-disponible-en-snapshot-demo',
    created_at: '2026-07-21T00:00:00Z',
    updated_at: '2026-07-21T00:00:00Z',
    review_action_count: 0,
    diagnostic: null,
  },
  effective_payload: payload,
  evidence: {
    ...commonEvidence,
    evidence_available: true,
    diagnostic: null,
    warnings: [],
    evidence_hash: 'no-disponible-en-snapshot-demo',
  },
  historical_structure_hash: null,
  current_structure_hash: structureHash,
  evidence_matches_current_structure: false,
  diagnostic: 'Comparación no disponible en el snapshot de demostración.',
}

export const demoTree: KnowledgeTreeResponse = {
  erps: [
    {
      erp_id: 'erp:cbmm',
      name: 'Cuerpo de Bomberos Municipal de Machala / CBMM',
      slug: 'cbmm',
      active_knowledge_version_id: versionId,
      knowledge_version: 'Snapshot demostrable verificado',
      modules: [
        {
          module_id: 'module:cuentasxcobrar',
          name: 'Cuentas por cobrar',
          route: null,
          available: true,
          diagnostic: null,
          order: 0,
          screens: [
            {
              screen_id: screenId,
              title: 'Retenciones',
              route: null,
              structural_review_status: 'approved',
              structural_available: true,
              diagnostic: null,
              semantic_state: 'pending_review',
              proposal_count: 1,
              pending_count: 1,
              latest_semantic_id: proposal.summary.semantic_id,
              latest_semantic_status: 'pending_review',
              capabilities_count: 2,
              evidence_available: true,
              warnings_count: 0,
            },
          ],
          counters,
        },
      ],
      unassigned_screens: [],
      warnings: [],
      counters,
    },
  ],
}

export const demoContexts: Record<string, ScreenReviewContextResponse> = {
  [screenId]: {
    erp: {
      erp_id: 'erp:cbmm',
      name: demoTree.erps[0]!.name,
      slug: 'cbmm',
    },
    version: {
      knowledge_version_id: versionId,
      knowledge_version: 'Snapshot demostrable verificado',
      status: 'active',
    },
    module: {
      module_id: 'module:cuentasxcobrar',
      name: 'Cuentas por cobrar',
      route: null,
    },
    screen: {
      screen_id: screenId,
      title: 'Retenciones',
      route: null,
      structural_review_status: 'approved',
      structural_available: true,
      diagnostic: null,
    },
    structural_evidence: {
      ...commonEvidence,
      evidence_available: true,
      diagnostic: null,
      warnings: [],
      current_structure_hash: structureHash,
    },
    semantic_proposals: [proposal],
    active_proposal: proposal,
    review_history: [],
    effective_payload: payload,
    traceability: {
      proposal_count: 1,
      review_action_count: 0,
      evidence_available: true,
      evidence_ids: [],
      warnings: [],
    },
    semantic_state: 'pending_review',
    navigation: {
      previous_screen_id: null,
      next_screen_id: null,
      module_screen_position: 1,
      module_screen_total: 1,
    },
    reviewer_identity_verified: false,
  },
}
