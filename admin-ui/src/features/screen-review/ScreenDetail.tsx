import { useEffect, useRef, useState } from "react";
import {
  AdminApiError,
  approveSemanticProposal,
  correctSemanticProposal,
  createSemanticInferenceJob,
  getPipelineJob,
  rejectSemanticProposal,
} from "../../api/client";
import type {
  AdminProposalSummary,
  CapabilityClaim,
  NetworkTraceEvidence,
  PipelineJobDetail,
  ScreenPurposeInference,
  ScreenReviewContextResponse,
} from "../../types/admin";
import { EmptyState } from "../../components/EmptyState";
import { StatusBadge } from "../../components/StatusBadge";

type Tab = "summary" | "structure" | "inference" | "traceability";
export type ScreenDetailMode = "full" | "structural" | "semantic";
type SemanticAction = "approve" | "correct" | "reject";
type EditableCapability = CapabilityClaim & { origin: "generated" | "human" };
type SemanticCorrectionDraft = Omit<
  ScreenPurposeInference,
  "supported_capabilities"
> & {
  supported_capabilities: EditableCapability[];
};
const allTabs: { id: Tab; label: string }[] = [
  { id: "summary", label: "Resumen" },
  { id: "structure", label: "Estructura" },
  { id: "inference", label: "Inferencia" },
  { id: "traceability", label: "Trazabilidad" },
];
const tabsForMode = (mode: ScreenDetailMode) =>
  mode === "structural"
    ? allTabs.filter(
        (item) => item.id === "structure" || item.id === "traceability",
      )
    : mode === "semantic"
      ? allTabs.filter((item) => item.id !== "structure")
      : allTabs;
const shortened = (value: string | null) =>
  value
    ? value.length > 22
      ? `${value.slice(0, 10)}…${value.slice(-8)}`
      : value
    : "No disponible en el snapshot de demostración";
const sleep = (ms: number) =>
  new Promise((resolve) => window.setTimeout(resolve, ms));
const messageOf = (error: unknown) =>
  error instanceof AdminApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : "Ocurrió un error inesperado.";
function correctionDraftFrom(
  value: ScreenPurposeInference | null,
): SemanticCorrectionDraft | null {
  if (!value) return null;
  return {
    ...value,
    supported_capabilities: value.supported_capabilities.map((claim) => ({
      ...claim,
      evidence_refs: [...claim.evidence_refs],
      origin: "generated",
    })),
    limitations: [...value.limitations],
    uncertainties: [...value.uncertainties],
  };
}

function formatDuration(value: number | null) {
  if (value === null) return "No registrado";
  const totalSeconds = Math.max(0, Math.floor(value / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function meaningfulEvidenceLabel(value: string | null) {
  const normalized = (value ?? "").trim().toLocaleLowerCase();
  return !["", "unlabeled control", "control sin etiqueta", "sin etiqueta"].includes(
    normalized,
  );
}

function claimableEvidenceIds(context: ScreenReviewContextResponse) {
  const evidence = context.structural_evidence;
  const values: string[] = [];

  for (const field of evidence.fields)
    if (meaningfulEvidenceLabel(field.label)) values.push(field.field_id);

  for (const control of evidence.controls)
    if (meaningfulEvidenceLabel(control.label)) values.push(control.control_id);

  for (const table of evidence.tables) {
    if (meaningfulEvidenceLabel(table.name)) values.push(table.table_id);
    for (const column of table.columns)
      if (meaningfulEvidenceLabel(column.label)) values.push(column.column_id);
  }

  const screenTitle = (evidence.screen_title ?? "").trim().toLocaleLowerCase();
  for (const state of evidence.ui_states) {
    const title = state.title.trim().toLocaleLowerCase();
    if (title && title !== screenTitle) values.push(state.state_id);
  }

  for (const event of evidence.events) {
    if (event.category.trim().toLocaleLowerCase() === "expand menu") continue;
    if (meaningfulEvidenceLabel(event.label)) values.push(event.event_id);
  }

  for (const transition of evidence.transitions) {
    const category = transition.category.trim().toLocaleLowerCase();
    if (category && category !== "expand menu")
      values.push(transition.transition_id);
  }

  return values;
}

function evidenceLabel(
  evidenceId: string,
  context: ScreenReviewContextResponse,
) {
  const evidence = context.structural_evidence;
  if (evidenceId === evidence.screen_id)
    return `Pantalla · ${evidence.screen_title ?? context.screen.title ?? "Sin título"}`;
  if (evidence.module?.module_id === evidenceId)
    return `Módulo · ${evidence.module.name}`;

  const field = evidence.fields.find((item) => item.field_id === evidenceId);
  if (field) return `Campo · ${field.label}`;

  const control = evidence.controls.find(
    (item) => item.control_id === evidenceId,
  );
  if (control) return `Control · ${control.label}`;

  for (const table of evidence.tables) {
    if (table.table_id === evidenceId) return `Tabla · ${table.name}`;
    const column = table.columns.find((item) => item.column_id === evidenceId);
    if (column) return `Columna · ${table.name} / ${column.label}`;
  }

  const state = evidence.ui_states.find((item) => item.state_id === evidenceId);
  if (state) return `Estado UI · ${state.title}`;

  const event = evidence.events.find((item) => item.event_id === evidenceId);
  if (event) return `Evento · ${event.label}`;

  const transition = evidence.transitions.find(
    (item) => item.transition_id === evidenceId,
  );
  if (transition) return `Transición · ${transition.category}`;

  const network = evidence.network_traces.find(
    (item) => item.evidence_id === evidenceId,
  );
  if (network) {
    const endpoints = network.endpoint_paths.slice(0, 2).join(", ");
    return `Network · ${endpoints || network.evidence_id}`;
  }

  return evidenceId;
}

function List({
  values,
  empty = "No disponible en el snapshot de demostración.",
}: {
  values: string[];
  empty?: string;
}) {
  return values.length ? (
    <ul className="detail-list">
      {values.map((value) => (
        <li key={value}>{value}</li>
      ))}
    </ul>
  ) : (
    <EmptyState>{empty}</EmptyState>
  );
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function networkTraceSummary(trace: NetworkTraceEvidence) {
  const values = [
    `Evidence: ${trace.evidence_id}`,
    `Métodos observados: ${trace.methods.length ? trace.methods.join(", ") : "Ninguno"}`,
    `Endpoints observados: ${trace.endpoint_paths.length ? trace.endpoint_paths.join(", ") : "Ninguno"}`,
    `Status observados: ${trace.status_codes.length ? trace.status_codes.join(", ") : "Ninguno"}`,
    `Recursos: ${trace.resource_types.length ? trace.resource_types.join(", ") : "Ninguno"}`,
    `Query keys: ${trace.query_keys.length ? trace.query_keys.join(", ") : "Ninguna"}`,
    `Observaciones: ${trace.observation_count}`,
    `Endpoints únicos: ${trace.endpoint_count}`,
    `Solo lectura: ${trace.read_only ? "Sí" : "No · excluida del prompt semántico"}`,
  ];
  return values.join(" · ");
}

function lifecycleTrace(summary: AdminProposalSummary) {
  if (summary.lifecycle_origin === "generated") return [];
  return [
    `Origen: ${summary.lifecycle_origin}`,
    `Source proposal: ${summary.source_semantic_proposal_id ?? "No disponible"}`,
    `Source version: ${summary.source_knowledge_version_id ?? "No disponible"}`,
    `Source review: ${summary.source_review_status ?? "No disponible"} · rev. ${summary.source_review_revision ?? "—"}`,
    `Source effective hash: ${shortened(summary.source_effective_content_hash)}`,
  ];
}

export function ScreenDetail({
  context,
  onNavigate,
  onRefresh,
  mode = "full",
}: {
  context: ScreenReviewContextResponse;
  onNavigate: (id: string) => void;
  onRefresh: () => void | Promise<void>;
  mode?: ScreenDetailMode;
}) {
  const availableTabs = tabsForMode(mode);
  const defaultTab: Tab = mode === "structural" ? "structure" : "summary";
  const [tab, setTab] = useState<Tab>(defaultTab);
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [correctionMode, setCorrectionMode] = useState(false);
  const [correctionDraft, setCorrectionDraft] =
    useState<SemanticCorrectionDraft | null>(null);
  const [reviewElapsedMs, setReviewElapsedMs] = useState(0);
  const reviewStartedAtRef = useRef<string | null>(null);
  const reviewStartedMsRef = useRef<number | null>(null);
  const [actionBusy, setActionBusy] = useState<SemanticAction | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [inferenceJob, setInferenceJob] = useState<PipelineJobDetail | null>(
    null,
  );

  const p = context.effective_payload;
  const e = context.structural_evidence;
  const proposal = context.active_proposal;
  const proposalStatus = proposal?.summary.current_review_status ?? null;
  const proposalFresh = proposal
    ? proposal.evidence_matches_current_structure !== false
    : true;
  const reviewable = proposalStatus === "pending_review" && proposalFresh;
  const inferenceAllowed = ["approved", "corrected"].includes(
    context.screen.structural_review_status,
  );

  useEffect(() => {
    setTab(defaultTab);
  }, [mode, defaultTab]);

  useEffect(() => {
    setCorrectionMode(false);
    setCorrectionDraft(
      correctionDraftFrom(proposal?.effective_payload ?? p ?? null),
    );
    setActionMessage(null);
    setActionError(null);
    setInferenceJob(null);

    if (
      mode !== "structural" &&
      proposal?.summary.current_review_status === "pending_review" &&
      proposalFresh
    ) {
      reviewStartedAtRef.current = new Date().toISOString();
      reviewStartedMsRef.current = Date.now();
      setReviewElapsedMs(0);
    } else {
      reviewStartedAtRef.current = null;
      reviewStartedMsRef.current = null;
      setReviewElapsedMs(0);
    }
  }, [
    context.screen.screen_id,
    proposal?.summary.semantic_id,
    proposal?.summary.review_revision,
    proposalFresh,
    mode,
  ]);

  useEffect(() => {
    if (!reviewable || reviewStartedMsRef.current === null) return;
    const timer = window.setInterval(() => {
      const started = reviewStartedMsRef.current;
      if (started !== null) setReviewElapsedMs(Date.now() - started);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [reviewable, context.screen.screen_id, proposal?.summary.semantic_id]);

  async function refreshAll() {
    await onRefresh();
  }

  async function runInference() {
    if (!inferenceAllowed) return;
    setActionError(null);
    setActionMessage(null);
    try {
      let job = await createSemanticInferenceJob({
        screen_id: context.screen.screen_id,
      });
      setInferenceJob(job);
      for (
        let attempt = 0;
        attempt < 240 && ["queued", "running"].includes(job.status);
        attempt += 1
      ) {
        await sleep(1000);
        job = await getPipelineJob(job.id);
        setInferenceJob(job);
      }
      if (job.status === "succeeded") {
        const origin = String(job.result_payload?.lifecycle_origin ?? "");
        setActionMessage(
          origin === "carried_forward"
            ? "Lifecycle completado: la semántica fue heredada sin llamar a Ollama."
            : origin === "reinferred"
              ? "Lifecycle completado: la semántica fue reinferida y requiere HITL."
              : "Lifecycle semántico completado.",
        );
        await refreshAll();
      } else if (job.status === "failed") {
        setActionError(
          job.error_summary ?? "La inferencia semántica fue rechazada o falló.",
        );
      } else if (["queued", "running"].includes(job.status)) {
        setActionError(
          "La inferencia continúa ejecutándose. Consulte el monitor de jobs para seguirla.",
        );
      }
    } catch (error: unknown) {
      setActionError(messageOf(error));
    }
  }

  async function performReview(action: SemanticAction) {
    if (!proposal || !reviewable) return;
    const cleanReviewer = reviewer.trim();
    const cleanReason = reason.trim();
    if (!cleanReviewer || !cleanReason) {
      setActionError(
        "Revisor y razón son obligatorios para registrar una decisión HITL.",
      );
      return;
    }
    setActionError(null);
    setActionMessage(null);
    const reviewStartedAt = reviewStartedAtRef.current;
    const reviewStartedMs = reviewStartedMsRef.current;
    if (!reviewStartedAt || reviewStartedMs === null) {
      setActionError("No se pudo iniciar el cronómetro de revisión.");
      return;
    }
    setActionBusy(action);
    const base = {
      reviewer_id: cleanReviewer,
      reason: cleanReason,
      expected_status: proposal.summary.current_review_status,
      expected_revision: proposal.summary.review_revision,
      review_started_at: reviewStartedAt,
      review_duration_ms: Math.max(0, Date.now() - reviewStartedMs),
    };
    try {
      if (action === "approve")
        await approveSemanticProposal(proposal.summary.semantic_id, base);
      if (action === "reject")
        await rejectSemanticProposal(proposal.summary.semantic_id, base);
      if (action === "correct") {
        if (!correctionDraft)
          throw new Error("No existe un borrador semántico para corregir.");

        const purposeSummary = correctionDraft.purpose_summary.trim();
        if (!purposeSummary)
          throw new Error("El propósito no puede quedar vacío.");

        const capabilities = correctionDraft.supported_capabilities.map(
          ({ origin: _origin, ...claim }) => ({
            statement: claim.statement.trim(),
            evidence_refs: [...new Set(claim.evidence_refs)],
          }),
        );

        if (capabilities.some((claim) => !claim.statement))
          throw new Error("Cada afirmación debe contener texto.");

        if (capabilities.some((claim) => claim.evidence_refs.length === 0))
          throw new Error(
            "Cada afirmación debe conservar al menos una evidencia.",
          );

        const corrected: ScreenPurposeInference = {
          semantic_type: correctionDraft.semantic_type,
          screen_id: correctionDraft.screen_id,
          purpose_summary: purposeSummary,
          supported_capabilities: capabilities,
          limitations: correctionDraft.limitations
            .map((item) => item.trim())
            .filter(Boolean),
          uncertainties: correctionDraft.uncertainties
            .map((item) => item.trim())
            .filter(Boolean),
        };

        const humanAddedClaims = correctionDraft.supported_capabilities
          .filter((claim) => claim.origin === "human")
          .map((claim) => ({
            statement: claim.statement.trim(),
            evidence_refs: [...new Set(claim.evidence_refs)],
            provenance: "human" as const,
          }));

        await correctSemanticProposal(proposal.summary.semantic_id, {
          ...base,
          corrected_payload: corrected,
          human_added_claims: humanAddedClaims,
        });
      }
      setActionMessage(
        action === "approve"
          ? "Propuesta semántica aprobada."
          : action === "reject"
            ? "Propuesta semántica rechazada."
            : "Propuesta semántica corregida y aceptada como corrección humana.",
      );
      setReviewer("");
      setReason("");
      setCorrectionMode(false);
      await refreshAll();
    } catch (error: unknown) {
      setActionError(messageOf(error));
    } finally {
      setActionBusy(null);
    }
  }

  return (
    <article className="detail">
      <header className="detail-header">
        <div>
          <p className="breadcrumb">
            {context.erp.name} <span>/</span>{" "}
            {context.module?.name ?? "Sin módulo"}
          </p>
          <div className="title-line">
            <h1>{context.screen.title ?? "Pantalla sin título"}</h1>
            <StatusBadge
              status={
                mode === "structural"
                  ? context.screen.structural_review_status
                  : context.semantic_state
              }
            />
          </div>
          <p className="route">
            {context.screen.route ??
              "Ruta no disponible en el snapshot de demostración"}
          </p>
        </div>
        <div className="position">
          <span>Posición en el módulo</span>
          <strong>
            {context.navigation.module_screen_position} de{" "}
            {context.navigation.module_screen_total}
          </strong>
        </div>
      </header>
      <div className="state-strip">
        <span>
          Estructura{" "}
          <StatusBadge status={context.screen.structural_review_status} />
        </span>
        <span>
          Semántica <StatusBadge status={context.semantic_state} />
        </span>
        <span>
          Evidencia{" "}
          <b>{e.evidence_available ? "Disponible" : "No disponible"}</b>
        </span>
      </div>
      {mode === "structural" ? (
        <section className="metrics" aria-label="Indicadores estructurales">
          <Metric label="Campos" value={e.fields.length} />
          <Metric label="Controles" value={e.controls.length} />
          <Metric label="Tablas" value={e.tables.length} />
          <Metric label="Transiciones" value={e.transitions.length} />
        </section>
      ) : (
        <section className="metrics" aria-label="Indicadores semánticos">
          <Metric
            label="Capabilities"
            value={p?.supported_capabilities.length ?? 0}
          />
          <Metric
            label="Origen lifecycle"
            value={proposal?.summary.lifecycle_origin ?? "No aplica"}
          />
          <Metric
            label="Acciones de revisión"
            value={context.traceability.review_action_count}
          />
          <Metric
            label="Evidencia vs. estructura"
            value={
              proposal &&
              (proposal.historical_structure_hash === null ||
                proposal.diagnostic ===
                  "Comparación no disponible en el snapshot de demostración.")
                ? "No disponible en el snapshot de demostración"
                : proposal?.evidence_matches_current_structure
                  ? "Coincide"
                  : proposal
                    ? "Difiere"
                    : "No aplica"
            }
          />
        </section>
      )}
      <div className="tabs" role="tablist" aria-label="Detalle de pantalla">
        {availableTabs.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <section className="tab-panel" role="tabpanel">
        {tab === "summary" && (
          <>
            <Section title="Propósito">
              <p className="purpose">
                {p?.purpose_summary ??
                  "La pantalla no tiene una propuesta semántica activa."}
              </p>
            </Section>
            <Section title="Capabilities">
              {p?.supported_capabilities.length ? (
                <div className="capabilities">
                  {p.supported_capabilities.map((capability) => (
                    <div
                      className="capability"
                      key={`${capability.statement}-${capability.evidence_refs.join("|")}`}
                    >
                      <span className="check" aria-hidden="true">
                        ✓
                      </span>
                      <div>
                        <strong>{capability.statement}</strong>
                        <small>
                          Evidencia: {capability.evidence_refs.join(", ")}
                        </small>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState>No hay capabilities disponibles.</EmptyState>
              )}
            </Section>
            <div className="two-cols">
              <Section title="Limitaciones">
                <List values={p?.limitations ?? []} />
              </Section>
              <Section title="Incertidumbres">
                <List values={p?.uncertainties ?? []} />
              </Section>
            </div>
            <SemanticGovernancePanel
              context={context}
              proposalFresh={proposalFresh}
              reviewer={reviewer}
              reason={reason}
              correctionMode={correctionMode}
              correctionDraft={correctionDraft}
              reviewElapsedMs={reviewElapsedMs}
              actionBusy={actionBusy}
              actionMessage={actionMessage}
              actionError={actionError}
              inferenceJob={inferenceJob}
              inferenceAllowed={inferenceAllowed}
              reviewable={reviewable}
              onReviewer={setReviewer}
              onReason={setReason}
              onCorrectionMode={setCorrectionMode}
              onCorrectionDraft={setCorrectionDraft}
              onInfer={() => void runInference()}
              onReview={(action) => void performReview(action)}
            />
          </>
        )}
        {tab === "structure" && (
          <div className="structure-grid">
            <EvidenceGroup
              title="Campos"
              values={e.fields.map((x) => x.label)}
            />
            <EvidenceGroup
              title="Controles"
              values={e.controls.map((x) => x.label)}
            />
            <EvidenceGroup
              title="Tablas y columnas"
              values={e.tables.map(
                (x) => `${x.name}: ${x.columns.map((c) => c.label).join(", ")}`,
              )}
            />
            <EvidenceGroup
              title="Estados UI"
              values={e.ui_states.map((x) => x.title)}
            />
            <EvidenceGroup
              title="Eventos"
              values={e.events.map((x) => x.label)}
            />
            <EvidenceGroup
              title="Transiciones"
              values={e.transitions.map((x) => x.transition_id)}
            />
            <EvidenceGroup
              title="Network Evidence · metadata agregada"
              values={e.network_traces.map(networkTraceSummary)}
            />
            <EvidenceGroup title="IDs de evidencia" values={e.evidence_ids} />
            <EvidenceGroup title="Advertencias" values={e.warnings} />
          </div>
        )}
        {tab === "inference" && (
          <>
            <div className="info-grid">
              <Metric
                label="Semantic ID"
                value={proposal?.summary.semantic_id ?? "No disponible"}
              />
              <Metric
                label="Tipo semántico"
                value={proposal?.summary.semantic_type ?? "No disponible"}
              />
              <Metric
                label="Estado"
                value={
                  proposal?.summary.current_review_status ?? "Sin propuesta"
                }
              />
              <Metric
                label="Origen lifecycle"
                value={proposal?.summary.lifecycle_origin ?? "No disponible"}
              />
              <Metric
                label="Modelo"
                value={proposal?.summary.generation_model ?? "No disponible"}
              />
              <Metric
                label="Prompt version"
                value={proposal?.summary.prompt_version ?? "No disponible"}
              />
              <Metric
                label="Evidence hash"
                value={shortened(proposal?.summary.evidence_hash ?? null)}
              />
            </div>
            {proposal && (
              <Section title="Provenance lifecycle">
                <List
                  values={lifecycleTrace(proposal.summary)}
                  empty="Propuesta generada en esta versión; no tiene source cross-version."
                />
              </Section>
            )}
            <Section title="Snapshot histórico">
              <p>
                {proposal?.evidence.evidence_available
                  ? `${proposal.evidence.evidence_ids.length} referencias de evidencia disponibles.`
                  : "Evidencia histórica no disponible."}
              </p>
            </Section>
            <Section title="Network Evidence del snapshot histórico">
              <List
                values={
                  proposal?.evidence.network_traces.map(networkTraceSummary) ??
                  []
                }
                empty="La propuesta no contiene Network Evidence segura en su snapshot."
              />
            </Section>
            <Section title="Comparación con la estructura actual">
              <p>
                {proposal &&
                (proposal.historical_structure_hash === null ||
                  proposal.diagnostic ===
                    "Comparación no disponible en el snapshot de demostración.")
                  ? "No disponible en el snapshot de demostración"
                  : proposal?.evidence_matches_current_structure
                    ? "La evidencia histórica coincide con la estructura actual."
                    : proposal
                      ? "La evidencia histórica difiere de la estructura actual."
                      : "No existe una propuesta para comparar."}
              </p>
            </Section>
            {proposal && (
              <Section title="Payload semántico efectivo">
                <pre className="semantic-json">
                  {JSON.stringify(proposal.effective_payload, null, 2)}
                </pre>
              </Section>
            )}
          </>
        )}
        {tab === "traceability" && (
          <>
            <div className="info-grid">
              <Metric
                label="Propuestas"
                value={context.traceability.proposal_count}
              />
              <Metric
                label="Acciones de revisión"
                value={context.traceability.review_action_count}
              />
              <Metric
                label="Identidad verificada"
                value={context.reviewer_identity_verified ? "Sí" : "No"}
              />
            </div>
            <Section title="IDs de evidencia">
              <List values={context.traceability.evidence_ids} />
            </Section>
            <Section title="Network Evidence actual">
              <List
                values={e.network_traces.map(networkTraceSummary)}
                empty="No existe Network Evidence segura para la estructura actual."
              />
            </Section>
            {proposal && (
              <Section title="Provenance lifecycle">
                <List
                  values={lifecycleTrace(proposal.summary)}
                  empty="Propuesta generada en esta versión; no tiene source cross-version."
                />
              </Section>
            )}
            <Section title="Historial">
              {context.review_history.length ? (
                <List
                  values={context.review_history.map(
                    (item) =>
                      `${item.action}: ${item.previous_status} → ${item.new_status} · ${item.reviewer_id} · tiempo ${formatDuration(item.review_duration_ms)} · adiciones humanas ${item.human_added_claims.length}`,
                  )}
                />
              ) : (
                <EmptyState>
                  No existen acciones de revisión registradas.
                </EmptyState>
              )}
            </Section>
            <Section title="Advertencias">
              <List
                values={context.traceability.warnings}
                empty="No existen advertencias."
              />
            </Section>
            <div className="navigation">
              <button
                disabled={!context.navigation.previous_screen_id}
                onClick={() =>
                  context.navigation.previous_screen_id &&
                  onNavigate(context.navigation.previous_screen_id)
                }
              >
                ← Pantalla anterior
              </button>
              <button
                disabled={!context.navigation.next_screen_id}
                onClick={() =>
                  context.navigation.next_screen_id &&
                  onNavigate(context.navigation.next_screen_id)
                }
              >
                Pantalla siguiente →
              </button>
            </div>
          </>
        )}
      </section>
    </article>
  );
}

function SemanticGovernancePanel({
  context,
  proposalFresh,
  reviewer,
  reason,
  correctionMode,
  correctionDraft,
  reviewElapsedMs,
  actionBusy,
  actionMessage,
  actionError,
  inferenceJob,
  inferenceAllowed,
  reviewable,
  onReviewer,
  onReason,
  onCorrectionMode,
  onCorrectionDraft,
  onInfer,
  onReview,
}: {
  context: ScreenReviewContextResponse;
  proposalFresh: boolean;
  reviewer: string;
  reason: string;
  correctionMode: boolean;
  correctionDraft: SemanticCorrectionDraft | null;
  reviewElapsedMs: number;
  actionBusy: SemanticAction | null;
  actionMessage: string | null;
  actionError: string | null;
  inferenceJob: PipelineJobDetail | null;
  inferenceAllowed: boolean;
  reviewable: boolean;
  onReviewer: (value: string) => void;
  onReason: (value: string) => void;
  onCorrectionMode: (value: boolean) => void;
  onCorrectionDraft: (value: SemanticCorrectionDraft | null) => void;
  onInfer: () => void;
  onReview: (action: SemanticAction) => void;
}) {
  const proposal = context.active_proposal;
  const inferenceRunning =
    inferenceJob && ["queued", "running"].includes(inferenceJob.status);

  const evidenceOptions = Array.from(
    new Set([
      ...claimableEvidenceIds(context),
      ...(correctionDraft?.supported_capabilities.flatMap(
        (claim) => claim.evidence_refs,
      ) ?? []),
    ]),
  );

  function updateDraft(
    update: (value: SemanticCorrectionDraft) => SemanticCorrectionDraft,
  ) {
    if (correctionDraft) onCorrectionDraft(update(correctionDraft));
  }

  function updateClaim(
    index: number,
    update: (value: EditableCapability) => EditableCapability,
  ) {
    updateDraft((draft) => ({
      ...draft,
      supported_capabilities: draft.supported_capabilities.map((claim, item) =>
        item === index ? update(claim) : claim,
      ),
    }));
  }

  return (
    <section className="semantic-governance" aria-label="Gobierno semántico">
      <div className="semantic-governance__head">
        <div>
          <p className="eyebrow">HITL SEMÁNTICO</p>
          <h2>Inferencia y revisión humana</h2>
          <p>
            El executor decide determinísticamente entre generar, heredar o
            reinferir; solo una propuesta nueva pendiente requiere una decisión
            HITL local.
          </p>
        </div>
        <div className="semantic-governance__status">
          <span>Identidad verificada</span>
          <strong>No · RBAC pendiente</strong>
        </div>
      </div>
      <div className="semantic-inference-launch">
        <div>
          <strong>
            {proposal ? "Reevaluar lifecycle semántico" : "Sin propuesta semántica"}
          </strong>
          <p>
            {inferenceAllowed
              ? proposal
                ? "El executor revalida la identidad de generación y la política vigente. Reutiliza una propuesta compatible o genera una nueva propuesta pendiente cuando el contrato cambió."
                : "La estructura está aprobada. El lifecycle decidirá carry-forward, reinferencia o generación sobre evidencia segura."
              : "El lifecycle requiere estructura aprobada/corregida y versión activa."}
          </p>
        </div>
        <button
          className="semantic-button semantic-button--primary"
          disabled={!inferenceAllowed || Boolean(inferenceRunning)}
          onClick={onInfer}
        >
          {inferenceRunning
            ? "Ejecutando…"
            : proposal
              ? "Reevaluar lifecycle"
              : "Ejecutar lifecycle semántico"}
        </button>
      </div>
      {inferenceJob && (
        <div className={`semantic-job semantic-job--${inferenceJob.status}`}>
          <strong>{stageLabel(inferenceJob.stage)}</strong>
          <span>
            {inferenceJob.progress_percent === null
              ? inferenceJob.status
              : `${Math.round(inferenceJob.progress_percent)} %`}
          </span>
          {inferenceJob.error_summary && (
            <small>{inferenceJob.error_summary}</small>
          )}
        </div>
      )}
      {proposal && (
        <>
          <div className="semantic-proposal-summary">
            <div>
              <span>Estado</span>
              <StatusBadge status={proposal.summary.current_review_status} />
            </div>
            <div>
              <span>Origen</span>
              <strong>{proposal.summary.lifecycle_origin}</strong>
            </div>
            <div>
              <span>Modelo</span>
              <strong>{proposal.summary.generation_model}</strong>
            </div>
            <div>
              <span>Revisión</span>
              <strong>{proposal.summary.review_revision}</strong>
            </div>
          </div>
          {!proposalFresh && (
            <div className="semantic-alert semantic-alert--warning">
              La proyección Safe Evidence comparable cambió. La UI bloquea
              aprobar/corregir y el backend vuelve a validar la evidencia
              exacta; la remediación debe ocurrir mediante un nuevo ciclo de
              conocimiento gobernado.
            </div>
          )}
          {proposal.summary.current_review_status === "pending_review" && (
            <div className="semantic-review-form">
              <label>
                <span>Revisor local</span>
                <input
                  value={reviewer}
                  maxLength={240}
                  placeholder="operador-demo"
                  onChange={(event) => onReviewer(event.target.value)}
                />
              </label>
              <label>
                <span>Razón / notas de revisión</span>
                <textarea
                  value={reason}
                  maxLength={4000}
                  rows={3}
                  placeholder="Verificado contra la evidencia estructural disponible."
                  onChange={(event) => onReason(event.target.value)}
                />
              </label>
              <div className="semantic-review-timer" aria-live="polite">
                <span>Tiempo de revisión</span>
                <strong>{formatDuration(reviewElapsedMs)}</strong>
                <small>Se registra automáticamente al tomar la decisión.</small>
              </div>

              <label className="semantic-correction-toggle">
                <input
                  type="checkbox"
                  checked={correctionMode}
                  onChange={(event) => onCorrectionMode(event.target.checked)}
                />
                <span>Corregir contenido o añadir conocimiento humano</span>
              </label>

              {correctionMode && correctionDraft && (
                <div className="semantic-structured-editor">
                  <label>
                    <span>Propósito de la pantalla</span>
                    <textarea
                      value={correctionDraft.purpose_summary}
                      rows={4}
                      onChange={(event) =>
                        updateDraft((draft) => ({
                          ...draft,
                          purpose_summary: event.target.value,
                        }))
                      }
                    />
                  </label>

                  <div className="semantic-claims-editor">
                    <div className="semantic-editor-heading">
                      <div>
                        <strong>Afirmaciones funcionales</strong>
                        <small>
                          Edite o elimine propuestas del LLM; las nuevas se
                          registran con provenance=human.
                        </small>
                      </div>

                      <button
                        className="semantic-button semantic-button--correct"
                        type="button"
                        onClick={() =>
                          updateDraft((draft) => ({
                            ...draft,
                            supported_capabilities: [
                              ...draft.supported_capabilities,
                              {
                                statement: "",
                                evidence_refs: [],
                                origin: "human",
                              },
                            ],
                          }))
                        }
                      >
                        + Añadir afirmación humana
                      </button>
                    </div>

                    {correctionDraft.supported_capabilities.map(
                      (claim, index) => (
                        <div
                          className="semantic-claim-editor"
                          key={`${claim.origin}-${index}`}
                        >
                          <div className="semantic-claim-editor__head">
                            <span
                              className={`semantic-origin semantic-origin--${claim.origin}`}
                            >
                              {claim.origin === "human"
                                ? "Añadida por humano"
                                : "Propuesta LLM"}
                            </span>

                            <button
                              type="button"
                              className="semantic-link-button semantic-link-button--danger"
                              onClick={() =>
                                updateDraft((draft) => ({
                                  ...draft,
                                  supported_capabilities:
                                    draft.supported_capabilities.filter(
                                      (_, item) => item !== index,
                                    ),
                                }))
                              }
                            >
                              Eliminar
                            </button>
                          </div>

                          <textarea
                            rows={3}
                            value={claim.statement}
                            placeholder="Describa una capacidad funcional respaldada por la evidencia."
                            onChange={(event) =>
                              updateClaim(index, (current) => ({
                                ...current,
                                statement: event.target.value,
                              }))
                            }
                          />

                          <div className="semantic-evidence-editor">
                            <strong>Evidencia</strong>

                            <div className="semantic-evidence-chips">
                              {claim.evidence_refs.map((evidenceId) => (
                                <span
                                  className="semantic-evidence-chip"
                                  key={evidenceId}
                                >
                                  <span>
                                    {evidenceLabel(evidenceId, context)}
                                    <small>{evidenceId}</small>
                                  </span>

                                  <button
                                    type="button"
                                    aria-label={`Quitar ${evidenceId}`}
                                    onClick={() =>
                                      updateClaim(index, (current) => ({
                                        ...current,
                                        evidence_refs:
                                          current.evidence_refs.filter(
                                            (item) => item !== evidenceId,
                                          ),
                                      }))
                                    }
                                  >
                                    ×
                                  </button>
                                </span>
                              ))}
                            </div>

                            <select
                              value=""
                              onChange={(event) => {
                                const evidenceId = event.target.value;
                                if (!evidenceId) return;

                                updateClaim(index, (current) => ({
                                  ...current,
                                  evidence_refs:
                                    current.evidence_refs.includes(evidenceId)
                                      ? current.evidence_refs
                                      : [
                                          ...current.evidence_refs,
                                          evidenceId,
                                        ],
                                }));
                              }}
                            >
                              <option value="">Añadir evidencia…</option>

                              {evidenceOptions
                                .filter(
                                  (evidenceId) =>
                                    !claim.evidence_refs.includes(evidenceId),
                                )
                                .map((evidenceId) => (
                                  <option value={evidenceId} key={evidenceId}>
                                    {evidenceLabel(evidenceId, context)}
                                  </option>
                                ))}
                            </select>
                          </div>
                        </div>
                      ),
                    )}
                  </div>

                  <div className="semantic-editor-columns">
                    <label>
                      <span>Limitaciones · una por línea</span>
                      <textarea
                        rows={5}
                        value={correctionDraft.limitations.join("\n")}
                        onChange={(event) =>
                          updateDraft((draft) => ({
                            ...draft,
                            limitations: event.target.value.split("\n"),
                          }))
                        }
                      />
                    </label>

                    <label>
                      <span>Incertidumbres · una por línea</span>
                      <textarea
                        rows={5}
                        value={correctionDraft.uncertainties.join("\n")}
                        onChange={(event) =>
                          updateDraft((draft) => ({
                            ...draft,
                            uncertainties: event.target.value.split("\n"),
                          }))
                        }
                      />
                    </label>
                  </div>

                  <small>
                    Los nombres de evidencia son legibles para la revisión, pero
                    el backend recibe y revalida los IDs gobernados exactos.
                  </small>
                </div>
              )}
              <div className="semantic-actions">
                <button
                  className="semantic-button semantic-button--approve"
                  disabled={
                    !reviewable || Boolean(actionBusy) || correctionMode
                  }
                  onClick={() => onReview("approve")}
                >
                  {actionBusy === "approve" ? "Aprobando…" : "Aprobar"}
                </button>
                <button
                  className="semantic-button semantic-button--correct"
                  disabled={
                    !reviewable ||
                    Boolean(actionBusy) ||
                    !correctionMode ||
                    !correctionDraft
                  }
                  onClick={() => onReview("correct")}
                >
                  {actionBusy === "correct"
                    ? "Corrigiendo…"
                    : "Guardar corrección"}
                </button>
                <button
                  className="semantic-button semantic-button--reject"
                  disabled={
                    !reviewable || Boolean(actionBusy) || correctionMode
                  }
                  onClick={() => onReview("reject")}
                >
                  {actionBusy === "reject" ? "Rechazando…" : "Rechazar"}
                </button>
              </div>
            </div>
          )}
          {proposal.summary.current_review_status !== "pending_review" &&
            proposal.summary.lifecycle_origin === "carried_forward" && (
              <div className="semantic-alert semantic-alert--success">
                Semántica publicable heredada por carry-forward desde una
                propuesta humana gobernada. No se creó una acción HITL local
                ficticia.
              </div>
            )}
          {proposal.summary.current_review_status !== "pending_review" &&
            proposal.summary.lifecycle_origin !== "carried_forward" && (
              <div className="semantic-alert semantic-alert--success">
                La propuesta ya tiene una decisión humana local:{" "}
                <strong>{proposal.summary.current_review_status}</strong>. La
                trazabilidad se conserva en el historial.
              </div>
            )}
        </>
      )}
      {actionMessage && (
        <div className="semantic-alert semantic-alert--success" role="status">
          {actionMessage}
        </div>
      )}
      {actionError && (
        <div className="semantic-alert semantic-alert--error" role="alert">
          {actionError}
        </div>
      )}
    </section>
  );
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    queued: "En cola",
    validating_active_screen: "Verificando pantalla activa",
    evidence_prepared: "Evidencia preparada",
    proposal_reused: "Reutilizando propuesta existente",
    carrying_forward_semantic_proposal: "Heredando semántica compatible",
    generating_semantic_proposal: "Generando o reinfiriendo con Ollama",
    semantic_lifecycle_blocked: "Lifecycle bloqueado",
    semantic_eligibility_rejected: "Elegibilidad semántica rechazada",
    semantic_generation_rejected: "Generación rechazada",
    proposal_ready: "Propuesta lista",
    completed: "Completado",
    failed: "Falló",
  };
  return labels[stage] ?? stage.replaceAll("_", " ");
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="section">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
function EvidenceGroup({ title, values }: { title: string; values: string[] }) {
  return (
    <section className="evidence-group">
      <h2>
        {title}
        <span>{values.length}</span>
      </h2>
      <List values={values} />
    </section>
  );
}
