import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AdminApiError,
  approveStructuralPublicationPackage,
  getStructuralPublicationReview,
} from "../../api/client";
import type {
  StructuralPublicationReviewPackage,
  StructuralPublicationReviewSummary,
  StructuralPublicationScope,
} from "../../types/admin";
import "./structural-publication-review.css";

type Props = {
  knowledgeVersionId: string | null;
  knowledgeVersion: string | null;
  onChanged?: () => void | Promise<void>;
};

type State = {
  summary: StructuralPublicationReviewSummary | null;
  selectedKey: string | null;
  loading: boolean;
  submitting: boolean;
  message: string | null;
  success: string | null;
};

const LIMIT = 50;
const scopeLabels: Record<StructuralPublicationScope, string> = {
  system: "Sistema",
  module: "Módulo",
  screen: "Pantalla",
  unscoped: "Sin alcance",
};

const errorMessage = (error: unknown) =>
  error instanceof AdminApiError
    ? error.message
    : error instanceof Error
      ? error.message
      : "Ocurrió un error inesperado en la revisión de publicación.";

const packageKey = (value: StructuralPublicationReviewPackage) =>
  `${value.scope_type}:${value.scope_id}`;

const shortHash = (value: string) => value.slice(0, 12);

export function StructuralPublicationReviewConsole({
  knowledgeVersionId,
  knowledgeVersion,
  onChanged,
}: Props) {
  const [state, setState] = useState<State>({
    summary: null,
    selectedKey: null,
    loading: Boolean(knowledgeVersionId),
    submitting: false,
    message: null,
    success: null,
  });
  const [pendingOnly, setPendingOnly] = useState(true);
  const [scopeType, setScopeType] = useState<StructuralPublicationScope | "">(
    "",
  );
  const [reviewerId, setReviewerId] = useState("");
  const [reason, setReason] = useState("");

  const load = useCallback(
    async (offset = 0) => {
      if (!knowledgeVersionId) {
        setState((old) => ({
          ...old,
          summary: null,
          selectedKey: null,
          loading: false,
          message: null,
        }));
        return;
      }

      setState((old) => ({ ...old, loading: true, message: null }));
      try {
        const summary = await getStructuralPublicationReview(
          knowledgeVersionId,
          {
            pendingOnly,
            scopeType: scopeType || undefined,
            limit: LIMIT,
            offset,
          },
        );
        setState((old) => {
          const previousSelection = summary.packages.some(
            (item) => packageKey(item) === old.selectedKey,
          )
            ? old.selectedKey
            : null;
          return {
            ...old,
            summary,
            selectedKey:
              previousSelection ??
              (summary.packages[0] ? packageKey(summary.packages[0]) : null),
            loading: false,
          };
        });
      } catch (error: unknown) {
        setState((old) => ({
          ...old,
          summary: null,
          selectedKey: null,
          loading: false,
          message: errorMessage(error),
        }));
      }
    },
    [knowledgeVersionId, pendingOnly, scopeType],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const selected = useMemo(
    () =>
      state.summary?.packages.find(
        (item) => packageKey(item) === state.selectedKey,
      ) ?? null,
    [state.selectedKey, state.summary],
  );

  const approve = async () => {
    if (
      !knowledgeVersionId ||
      !selected ||
      selected.pending_count === 0 ||
      !reviewerId.trim() ||
      !reason.trim() ||
      state.submitting
    ) {
      return;
    }

    setState((old) => ({
      ...old,
      submitting: true,
      message: null,
      success: null,
    }));
    try {
      const result = await approveStructuralPublicationPackage(
        knowledgeVersionId,
        {
          scope_type: selected.scope_type,
          scope_id: selected.scope_id,
          expected_package_hash: selected.package_hash,
          reviewer_id: reviewerId.trim(),
          reason: reason.trim(),
        },
      );
      setState((old) => ({
        ...old,
        submitting: false,
        success: `${result.approved_count} elementos pendientes fueron aprobados. Las proyecciones estructurales requieren sincronización nuevamente.`,
      }));
      await load(state.summary?.offset ?? 0);
      if (onChanged) {
        try {
          await onChanged();
        } catch {
          // La aprobación ya fue confirmada por el backend; un fallo de refresco no la revierte.
        }
      }
    } catch (error: unknown) {
      setState((old) => ({
        ...old,
        submitting: false,
        message: errorMessage(error),
      }));
    }
  };

  const summary = state.summary;
  const previousOffset = summary ? Math.max(0, summary.offset - LIMIT) : 0;
  const hasPrevious = Boolean(summary && summary.offset > 0);
  const hasNext = Boolean(
    summary?.next_offset !== null && summary?.next_offset !== undefined,
  );
  const firstVisible = summary && summary.total > 0 ? summary.offset + 1 : 0;

  return (
    <section
      className="publication-review"
      aria-label="Revisión estructural de publicación"
    >
      <div className="publication-review__heading">
        <div>
          <span>Structural Publication Review</span>
          <h2>Cobertura publicable de la ACTIVE</h2>
          <p>
            Revisa paquetes de la versión ACTIVE y aprueba únicamente elementos
            pendientes. Los elementos rechazados requieren revisión individual;
            esta acción no los modifica.
          </p>
        </div>
        <button
          onClick={() => void load(summary?.offset ?? 0)}
          disabled={!knowledgeVersionId || state.loading}
        >
          Actualizar
        </button>
      </div>

      {!knowledgeVersionId && (
        <div className="publication-review__notice">
          No existe una versión ACTIVE disponible para revisar.
        </div>
      )}
      {state.message && (
        <div className="publication-review__message" role="alert">
          {state.message}
        </div>
      )}
      {state.success && (
        <div className="publication-review__success" role="status">
          {state.success}
        </div>
      )}

      <div className="publication-review__toolbar">
        <div className="publication-review__active">
          <span>ACTIVE</span>
          <strong>
            {knowledgeVersion ?? summary?.knowledge_version ?? "—"}
          </strong>
          {summary && <code>{summary.knowledge_version_id}</code>}
        </div>
        <label className="publication-review__filter">
          <span>Alcance</span>
          <select
            value={scopeType}
            onChange={(event) =>
              setScopeType(
                event.target.value as StructuralPublicationScope | "",
              )
            }
          >
            <option value="">Todos</option>
            <option value="system">Sistema</option>
            <option value="module">Módulo</option>
            <option value="screen">Pantalla</option>
            <option value="unscoped">Sin alcance</option>
          </select>
        </label>
        <label className="publication-review__check">
          <input
            type="checkbox"
            checked={pendingOnly}
            onChange={(event) => setPendingOnly(event.target.checked)}
          />
          <span>Solo paquetes con pendientes</span>
        </label>
      </div>

      {summary && (
        <>
          <div className="publication-review__metrics">
            <div>
              <strong>{summary.publishable_count}</strong>
              <span>Publicables</span>
            </div>
            <div>
              <strong>{summary.pending_count}</strong>
              <span>Pendientes</span>
            </div>
            <div>
              <strong>{summary.rejected_count}</strong>
              <span>Rechazados</span>
            </div>
            <div>
              <strong>{summary.package_count}</strong>
              <span>Paquetes totales</span>
            </div>
          </div>

          {summary.pending_count === 0 && summary.rejected_count === 0 && (
            <div className="publication-review__closed">
              <strong>Cobertura estructural cerrada.</strong>
              <span>
                No quedan elementos pendientes ni rechazados en la ACTIVE.
              </span>
            </div>
          )}

          <div className="publication-review__layout">
            <article className="publication-review__card publication-review__list-card">
              <header>
                <div>
                  <span>Paquetes</span>
                  <h3>{summary.total} coinciden con el filtro</h3>
                </div>
                <small>
                  {firstVisible}-
                  {Math.min(
                    summary.offset + summary.packages.length,
                    summary.total,
                  )}{" "}
                  de {summary.total}
                </small>
              </header>
              <div className="publication-review__packages">
                {summary.packages.length === 0 && (
                  <div className="publication-review__empty">
                    No hay paquetes para el filtro seleccionado.
                  </div>
                )}
                {summary.packages.map((item) => {
                  const key = packageKey(item);
                  const status =
                    item.pending_count > 0
                      ? "pending"
                      : item.rejected_count > 0
                        ? "rejected"
                        : "ready";
                  return (
                    <button
                      key={key}
                      className={key === state.selectedKey ? "is-selected" : ""}
                      onClick={() =>
                        setState((old) => ({
                          ...old,
                          selectedKey: key,
                          success: null,
                        }))
                      }
                    >
                      <span className="publication-review__package-main">
                        <strong>{item.title ?? item.scope_id}</strong>
                        <small>
                          {scopeLabels[item.scope_type]} ·{" "}
                          {item.route ?? item.scope_id}
                        </small>
                      </span>
                      <em
                        className={`publication-review__status publication-review__status--${status}`}
                      >
                        {item.pending_count > 0
                          ? `${item.pending_count} pendientes`
                          : item.rejected_count > 0
                            ? `${item.rejected_count} rechazados`
                            : "cerrado"}
                      </em>
                    </button>
                  );
                })}
              </div>
              <footer className="publication-review__pagination">
                <button
                  onClick={() => void load(previousOffset)}
                  disabled={!hasPrevious || state.loading}
                >
                  Anterior
                </button>
                <button
                  onClick={() =>
                    void load(summary.next_offset ?? summary.offset)
                  }
                  disabled={!hasNext || state.loading}
                >
                  Siguiente
                </button>
              </footer>
            </article>

            <article className="publication-review__card publication-review__detail-card">
              {!selected && (
                <div className="publication-review__empty publication-review__empty--detail">
                  Seleccione un paquete para inspeccionarlo.
                </div>
              )}
              {selected && (
                <>
                  <header>
                    <div>
                      <span>{scopeLabels[selected.scope_type]}</span>
                      <h3>{selected.title ?? selected.scope_id}</h3>
                    </div>
                    <code>{shortHash(selected.package_hash)}</code>
                  </header>

                  <dl className="publication-review__meta">
                    <div>
                      <dt>Scope ID</dt>
                      <dd>{selected.scope_id}</dd>
                    </div>
                    <div>
                      <dt>Ruta</dt>
                      <dd>{selected.route ?? "—"}</dd>
                    </div>
                    <div>
                      <dt>Módulo</dt>
                      <dd>{selected.module_id ?? "—"}</dd>
                    </div>
                    <div>
                      <dt>Module path</dt>
                      <dd>
                        {selected.module_path.length > 0
                          ? selected.module_path.join(" → ")
                          : "—"}
                      </dd>
                    </div>
                  </dl>

                  <div className="publication-review__package-counts">
                    <div>
                      <strong>{selected.publishable_count}</strong>
                      <span>Publicables</span>
                    </div>
                    <div>
                      <strong>{selected.pending_count}</strong>
                      <span>Pendientes</span>
                    </div>
                    <div>
                      <strong>{selected.rejected_count}</strong>
                      <span>Rechazados</span>
                    </div>
                  </div>

                  <div className="publication-review__entities">
                    <h4>Entidades del paquete</h4>
                    <div>
                      {Object.entries(selected.entity_counts).map(
                        ([entityType, count]) => (
                          <span key={entityType}>
                            <strong>{count}</strong>
                            {entityType}
                          </span>
                        ),
                      )}
                    </div>
                  </div>

                  <div className="publication-review__items">
                    <h4>Elementos no publicables</h4>
                    {selected.review_items.length === 0 ? (
                      <p>
                        El paquete no contiene elementos pendientes ni
                        rechazados.
                      </p>
                    ) : (
                      <div>
                        {selected.review_items.map((item) => (
                          <article key={item.item_id}>
                            <div>
                              <strong>{item.title ?? item.canonical_id}</strong>
                              <small>
                                {item.entity_type} · rev. {item.review_revision}
                              </small>
                            </div>
                            <em
                              className={`publication-review__item-status publication-review__item-status--${item.review_status}`}
                            >
                              {item.review_status}
                            </em>
                          </article>
                        ))}
                      </div>
                    )}
                  </div>

                  {selected.rejected_count > 0 && (
                    <div className="publication-review__warning">
                      Este paquete contiene elementos rechazados. La aprobación
                      masiva sólo cambia los elementos{" "}
                      <code>pending_review</code>; los rechazados deben
                      resolverse desde la revisión estructural.
                    </div>
                  )}

                  {selected.pending_count > 0 ? (
                    <div className="publication-review__form">
                      <label>
                        <span>Revisor</span>
                        <input
                          value={reviewerId}
                          onChange={(event) =>
                            setReviewerId(event.target.value)
                          }
                          placeholder="operador / usuario"
                        />
                      </label>
                      <label>
                        <span>Razón</span>
                        <textarea
                          rows={4}
                          value={reason}
                          onChange={(event) => setReason(event.target.value)}
                          placeholder="Criterio usado para cerrar este paquete de publicación"
                        />
                      </label>
                      <button
                        onClick={() => void approve()}
                        disabled={
                          !reviewerId.trim() ||
                          !reason.trim() ||
                          state.submitting
                        }
                      >
                        Aprobar {selected.pending_count} pendientes del paquete
                      </button>
                    </div>
                  ) : (
                    <div className="publication-review__ready">
                      <strong>Sin pendientes.</strong>
                      <span>
                        {selected.rejected_count > 0
                          ? "Todavía existen rechazados que requieren resolución individual."
                          : "El paquete está completamente publicable."}
                      </span>
                    </div>
                  )}
                </>
              )}
            </article>
          </div>
        </>
      )}
    </section>
  );
}
