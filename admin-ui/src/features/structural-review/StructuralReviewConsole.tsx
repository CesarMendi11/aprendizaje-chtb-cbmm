import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AdminApiError,
  approveStructuralReviewItem,
  correctStructuralReviewItem,
  getPipelineJobs,
  getStructuralReviewItem,
  getStructuralReviewItems,
  rejectStructuralReviewItem,
  resetStructuralReviewItem,
} from "../../api/client";
import type {
  PipelineJobSummary,
  ReviewStatus,
  StructuralReviewItemDetail,
  StructuralReviewItemSummary,
} from "../../types/admin";
import "./structural-review.css";

type FilterStatus = "all" | ReviewStatus;

type ReviewState = {
  versions: PipelineJobSummary[];
  versionId: string | null;
  items: StructuralReviewItemSummary[];
  detail: StructuralReviewItemDetail | null;
  total: number;
  counts: Record<string, number>;
  loadingVersions: boolean;
  loadingItems: boolean;
  loadingDetail: boolean;
  submitting: boolean;
  message: string | null;
};

const errorMessage = (error: unknown) =>
  error instanceof AdminApiError
    ? error.message
    : "Ocurrió un error inesperado durante la revisión estructural.";
const titleOf = (item: StructuralReviewItemSummary) =>
  item.title?.trim() || item.route || item.canonical_id;
const statusLabel = (status: string) =>
  ({
    pending_review: "Pendiente",
    approved: "Aprobado",
    corrected: "Corregido",
    rejected: "Rechazado",
  })[status] ?? status;
const entityLabel = (type: string) =>
  ({
    erp_system: "ERP",
    module: "Módulo",
    screen: "Pantalla",
    ui_state: "Estado UI",
    field: "Campo",
    control: "Control",
    table: "Tabla",
    table_column: "Columna",
    link: "Enlace",
    event: "Evento",
    transition: "Transición",
    evidence: "Evidencia",
  })[type] ?? type.replaceAll("_", " ");
const when = (value: string) => new Date(value).toLocaleString();
const stringify = (value: Record<string, unknown>) =>
  JSON.stringify(value, null, 2);

function Count({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div
      className={`structural-count ${tone ? `structural-count--${tone}` : ""}`}
    >
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function JsonBlock({ value }: { value: Record<string, unknown> }) {
  return <pre className="structural-json">{stringify(value)}</pre>;
}

export function StructuralReviewConsole() {
  const [state, setState] = useState<ReviewState>({
    versions: [],
    versionId: null,
    items: [],
    detail: null,
    total: 0,
    counts: {},
    loadingVersions: true,
    loadingItems: false,
    loadingDetail: false,
    submitting: false,
    message: null,
  });
  const [status, setStatus] = useState<FilterStatus>("pending_review");
  const [entityType, setEntityType] = useState("");
  const [search, setSearch] = useState("");
  const [reviewerId, setReviewerId] = useState("");
  const [reason, setReason] = useState("");
  const [correctionText, setCorrectionText] = useState("");
  const [showCorrection, setShowCorrection] = useState(false);

  const loadVersions = useCallback(async () => {
    setState((old) => ({ ...old, loadingVersions: true, message: null }));
    try {
      const response = await getPipelineJobs(50, "canonical_import");
      const versions = response.items.filter(
        (job) =>
          job.status === "succeeded" && Boolean(job.knowledge_version_id),
      );
      setState((old) => ({
        ...old,
        versions,
        versionId: old.versionId ?? versions[0]?.knowledge_version_id ?? null,
        loadingVersions: false,
      }));
    } catch (error: unknown) {
      setState((old) => ({
        ...old,
        loadingVersions: false,
        message: errorMessage(error),
      }));
    }
  }, []);

  useEffect(() => {
    void loadVersions();
  }, [loadVersions]);

  const loadItems = useCallback(
    async (versionId: string, keepSelection = true) => {
      setState((old) => ({ ...old, loadingItems: true, message: null }));
      try {
        const response = await getStructuralReviewItems({
          knowledgeVersionId: versionId,
          status: status === "all" ? undefined : status,
          entityType: entityType || undefined,
          search: search || undefined,
          limit: 100,
        });
        setState((old) => {
          const selectedStillExists =
            keepSelection &&
            old.detail &&
            response.items.some((item) => item.id === old.detail?.id);
          return {
            ...old,
            items: response.items,
            total: response.total,
            counts: response.status_counts,
            loadingItems: false,
            detail: selectedStillExists ? old.detail : null,
          };
        });
      } catch (error: unknown) {
        setState((old) => ({
          ...old,
          loadingItems: false,
          message: errorMessage(error),
        }));
      }
    },
    [entityType, search, status],
  );

  useEffect(() => {
    const versionId = state.versionId;
    if (!versionId) return;
    const timer = window.setTimeout(
      () => void loadItems(versionId, false),
      180,
    );
    return () => window.clearTimeout(timer);
  }, [loadItems, state.versionId]);

  const selectItem = useCallback(async (itemId: string) => {
    setState((old) => ({ ...old, loadingDetail: true, message: null }));
    try {
      const detail = await getStructuralReviewItem(itemId);
      setState((old) => ({ ...old, detail, loadingDetail: false }));
      setReason("");
      setCorrectionText(stringify(detail.effective_payload));
      setShowCorrection(false);
    } catch (error: unknown) {
      setState((old) => ({
        ...old,
        loadingDetail: false,
        message: errorMessage(error),
      }));
    }
  }, []);

  const detail = state.detail;
  const canApprove =
    detail?.current_review_status === "pending_review" ||
    detail?.current_review_status === "corrected";
  const canReject =
    detail?.current_review_status === "pending_review" ||
    detail?.current_review_status === "approved" ||
    detail?.current_review_status === "corrected";
  const canCorrect =
    detail?.current_review_status === "pending_review" ||
    detail?.current_review_status === "approved";
  const canReset = detail?.current_review_status === "rejected";

  const submit = async (action: "approve" | "reject" | "reset" | "correct") => {
    if (!detail || !state.versionId || !reviewerId.trim() || state.submitting)
      return;
    if ((action === "reject" || action === "correct") && !reason.trim()) {
      setState((old) => ({
        ...old,
        message: "El rechazo y la corrección requieren una razón.",
      }));
      return;
    }
    setState((old) => ({ ...old, submitting: true, message: null }));
    const base = {
      reviewer_id: reviewerId.trim(),
      reason: reason.trim() || null,
      expected_status: detail.current_review_status,
      expected_revision: detail.review_revision,
    };
    try {
      let updated: StructuralReviewItemDetail;
      if (action === "approve")
        updated = await approveStructuralReviewItem(detail.id, base);
      else if (action === "reject")
        updated = await rejectStructuralReviewItem(detail.id, base);
      else if (action === "reset")
        updated = await resetStructuralReviewItem(detail.id, base);
      else {
        let corrected: unknown;
        try {
          corrected = JSON.parse(correctionText);
        } catch {
          throw new Error("El payload corregido no contiene JSON válido.");
        }
        if (
          typeof corrected !== "object" ||
          corrected === null ||
          Array.isArray(corrected)
        )
          throw new Error("El payload corregido debe ser un objeto JSON.");
        updated = await correctStructuralReviewItem(detail.id, {
          ...base,
          reason: reason.trim(),
          corrected_payload: corrected as Record<string, unknown>,
        });
      }
      setState((old) => ({
        ...old,
        detail: updated,
        submitting: false,
        message: null,
      }));
      setCorrectionText(stringify(updated.effective_payload));
      setReason("");
      setShowCorrection(false);
      await loadItems(state.versionId, true);
    } catch (error: unknown) {
      const message =
        error instanceof Error ? error.message : errorMessage(error);
      setState((old) => ({ ...old, submitting: false, message }));
    }
  };

  const selectedVersion = useMemo(
    () =>
      state.versions.find(
        (job) => job.knowledge_version_id === state.versionId,
      ) ?? null,
    [state.versionId, state.versions],
  );
  const versionText =
    detail?.knowledge_version ??
    selectedVersion?.knowledge_version_id?.slice(0, 8) ??
    "—";

  return (
    <section
      className="structural-review"
      aria-label="Revisión estructural HITL"
    >
      <div className="structural-review__heading">
        <div>
          <span className="structural-eyebrow">HITL estructural</span>
          <h2>Revisión humana del conocimiento</h2>
          <p>
            Revise la versión importada antes de cualquier publicación. Las
            decisiones quedan persistidas y no activan ni sincronizan la versión
            automáticamente.
          </p>
        </div>
        <button
          onClick={() => void loadVersions()}
          disabled={state.loadingVersions}
        >
          Actualizar versiones
        </button>
      </div>

      {state.message && (
        <div className="structural-error" role="alert">
          {state.message}
        </div>
      )}

      <div className="structural-toolbar">
        <label>
          <span>Versión staging</span>
          <select
            value={state.versionId ?? ""}
            onChange={(event) =>
              setState((old) => ({
                ...old,
                versionId: event.target.value || null,
                detail: null,
              }))
            }
            disabled={state.loadingVersions || state.versions.length === 0}
          >
            <option value="">Sin importaciones</option>
            {state.versions.map((job) => (
              <option key={job.id} value={job.knowledge_version_id ?? ""}>
                {job.target ?? job.scope} · {when(job.requested_at)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Estado</span>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as FilterStatus)}
          >
            <option value="pending_review">Pendientes</option>
            <option value="approved">Aprobados</option>
            <option value="corrected">Corregidos</option>
            <option value="rejected">Rechazados</option>
            <option value="all">Todos</option>
          </select>
        </label>
        <label>
          <span>Tipo</span>
          <select
            value={entityType}
            onChange={(event) => setEntityType(event.target.value)}
          >
            <option value="">Todos</option>
            <option value="screen">Pantalla</option>
            <option value="field">Campo</option>
            <option value="control">Control</option>
            <option value="table">Tabla</option>
            <option value="table_column">Columna</option>
            <option value="link">Enlace</option>
            <option value="ui_state">Estado UI</option>
            <option value="module">Módulo</option>
            <option value="evidence">Evidencia</option>
            <option value="erp_system">ERP</option>
          </select>
        </label>
        <label className="structural-search">
          <span>Buscar</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Título, canonical ID o ruta"
          />
        </label>
      </div>

      <div className="structural-counts">
        <Count
          label="Pendientes"
          value={state.counts.pending_review ?? 0}
          tone="pending"
        />
        <Count
          label="Aprobados"
          value={state.counts.approved ?? 0}
          tone="approved"
        />
        <Count
          label="Corregidos"
          value={state.counts.corrected ?? 0}
          tone="corrected"
        />
        <Count
          label="Rechazados"
          value={state.counts.rejected ?? 0}
          tone="rejected"
        />
        <Count label="Mostrados" value={state.total} />
      </div>

      <div className="structural-layout">
        <article className="structural-list-card">
          <div className="structural-card-head">
            <div>
              <span>Cola de revisión</span>
              <h3>
                {state.loadingItems
                  ? "Consultando…"
                  : `${state.total} elementos`}
              </h3>
            </div>
            <span className="structural-version">{versionText}</span>
          </div>
          <div className="structural-list">
            {!state.loadingItems && state.items.length === 0 && (
              <p className="structural-empty">
                No hay elementos para los filtros seleccionados.
              </p>
            )}
            {state.items.map((item) => (
              <button
                key={item.id}
                className={detail?.id === item.id ? "is-selected" : ""}
                onClick={() => void selectItem(item.id)}
              >
                <span className="structural-item-main">
                  <strong>{titleOf(item)}</strong>
                  <small>
                    {entityLabel(item.entity_type)} · rev.{" "}
                    {item.review_revision}
                  </small>
                </span>
                <span
                  className={`structural-status structural-status--${item.current_review_status}`}
                >
                  {statusLabel(item.current_review_status)}
                </span>
              </button>
            ))}
          </div>
        </article>

        <article className="structural-detail-card">
          {!detail && !state.loadingDetail && (
            <div className="structural-empty structural-empty--detail">
              <strong>Seleccione un elemento</strong>
              <span>
                Se mostrará su payload original, valor efectivo, historial y
                acciones HITL.
              </span>
            </div>
          )}
          {state.loadingDetail && (
            <div className="structural-empty structural-empty--detail">
              <span className="spinner" />
              Cargando elemento…
            </div>
          )}
          {detail && (
            <>
              <div className="structural-card-head">
                <div>
                  <span>{entityLabel(detail.entity_type)}</span>
                  <h3>{titleOf(detail)}</h3>
                  <code>{detail.canonical_id}</code>
                </div>
                <span
                  className={`structural-status structural-status--${detail.current_review_status}`}
                >
                  {statusLabel(detail.current_review_status)}
                </span>
              </div>
              <div className="structural-meta">
                <span>
                  Versión <b>{detail.knowledge_version}</b>
                </span>
                <span>
                  Estado versión <b>{detail.version_status}</b>
                </span>
                <span>
                  Revisión <b>{detail.review_revision}</b>
                </span>
                <span>
                  Identidad verificada <b>No</b>
                </span>
              </div>

              <div className="structural-payloads">
                <details open>
                  <summary>Payload efectivo</summary>
                  <JsonBlock value={detail.effective_payload} />
                </details>
                <details>
                  <summary>Payload capturado original</summary>
                  <JsonBlock value={detail.source_payload} />
                </details>
              </div>

              <div className="structural-review-form">
                <label>
                  <span>Revisor local</span>
                  <input
                    value={reviewerId}
                    onChange={(event) => setReviewerId(event.target.value)}
                    maxLength={240}
                    placeholder="Ej. operador-demo"
                  />
                </label>
                <label>
                  <span>Razón / notas</span>
                  <textarea
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    maxLength={4000}
                    rows={3}
                    placeholder="Obligatoria para corregir o rechazar"
                  />
                </label>
                {showCorrection && (
                  <label className="structural-correction">
                    <span>Payload corregido · JSON canónico</span>
                    <textarea
                      value={correctionText}
                      onChange={(event) =>
                        setCorrectionText(event.target.value)
                      }
                      rows={16}
                      spellCheck={false}
                    />
                    <small>
                      Debe conservar el canonical ID y las relaciones críticas.
                      El backend vuelve a validar el modelo canónico.
                    </small>
                  </label>
                )}
                <div className="structural-review-actions">
                  <button
                    className="approve"
                    onClick={() => void submit("approve")}
                    disabled={
                      !canApprove || !reviewerId.trim() || state.submitting
                    }
                  >
                    Aprobar
                  </button>
                  <button
                    className="correct"
                    onClick={() => setShowCorrection((value) => !value)}
                    disabled={!canCorrect || state.submitting}
                  >
                    {showCorrection ? "Cerrar corrección" : "Corregir"}
                  </button>
                  {showCorrection && (
                    <button
                      className="correct-save"
                      onClick={() => void submit("correct")}
                      disabled={
                        !canCorrect ||
                        !reviewerId.trim() ||
                        !reason.trim() ||
                        state.submitting
                      }
                    >
                      Guardar corrección
                    </button>
                  )}
                  <button
                    className="reject"
                    onClick={() => void submit("reject")}
                    disabled={
                      !canReject ||
                      !reviewerId.trim() ||
                      !reason.trim() ||
                      state.submitting
                    }
                  >
                    Rechazar
                  </button>
                  <button
                    onClick={() => void submit("reset")}
                    disabled={
                      !canReset || !reviewerId.trim() || state.submitting
                    }
                  >
                    Volver a pendiente
                  </button>
                </div>
              </div>

              <div className="structural-history">
                <h4>Historial de revisión</h4>
                {detail.review_history.length === 0 ? (
                  <p>Sin acciones humanas registradas.</p>
                ) : (
                  <ol>
                    {detail.review_history.map((entry, index) => (
                      <li key={`${entry.created_at}-${index}`}>
                        <strong>{entry.action}</strong>
                        <span>
                          {statusLabel(entry.previous_status)} →{" "}
                          {statusLabel(entry.new_status)}
                        </span>
                        <small>
                          {entry.reviewer_id ?? "sin revisor"} · {entry.source}{" "}
                          · {when(entry.created_at)}
                        </small>
                        {entry.reason && <p>{entry.reason}</p>}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </>
          )}
        </article>
      </div>
    </section>
  );
}
