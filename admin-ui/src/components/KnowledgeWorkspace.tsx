import { KnowledgeSidebar } from "../features/knowledge-tree/KnowledgeSidebar";
import {
  ScreenDetail,
  type ScreenDetailMode,
} from "../features/screen-review/ScreenDetail";
import type {
  KnowledgeTreeErp,
  ScreenReviewContextResponse,
} from "../types/admin";

type DetailState =
  | { status: "loading"; data?: ScreenReviewContextResponse }
  | { status: "ready"; data: ScreenReviewContextResponse }
  | { status: "error"; message: string; data?: ScreenReviewContextResponse }
  | null;

export function KnowledgeWorkspace({
  erp,
  selectedId,
  detail,
  mode,
  treeMessage,
  onSelect,
  onRetryTree,
  onRetryDetail,
  onRefresh,
}: {
  erp: KnowledgeTreeErp;
  selectedId: string | null;
  detail: DetailState;
  mode: ScreenDetailMode;
  treeMessage?: string | null;
  onSelect: (id: string) => void;
  onRetryTree: () => void;
  onRetryDetail: () => void;
  onRefresh: () => void | Promise<void>;
}) {
  return (
    <div className="knowledge-workspace">
      <KnowledgeSidebar erp={erp} selectedId={selectedId} onSelect={onSelect} />
      <div className="knowledge-main">
        {treeMessage && (
          <div className="inline-error" role="alert">
            {treeMessage}{" "}
            <button onClick={onRetryTree}>Reintentar árbol</button>
          </div>
        )}
        {detail?.status === "loading" && !detail.data && (
          <div className="knowledge-loading" aria-live="polite">
            <span className="spinner" />
            Cargando detalle de pantalla…
          </div>
        )}
        {detail?.status === "error" && !detail.data && (
          <KnowledgeError message={detail.message} retry={onRetryDetail} />
        )}
        {detail?.data && (
          <>
            <ScreenDetail
              context={detail.data}
              mode={mode}
              onNavigate={onSelect}
              onRefresh={onRefresh}
            />
            {detail.status === "error" && (
              <div className="detail-overlay-error" role="alert">
                No se pudo actualizar el detalle: {detail.message}{" "}
                <button onClick={onRetryDetail}>Reintentar</button>
              </div>
            )}
          </>
        )}
        {!detail && (
          <div className="knowledge-loading">
            Seleccione una pantalla para consultar su contexto.
          </div>
        )}
      </div>
    </div>
  );
}

function KnowledgeError({
  message,
  retry,
}: {
  message: string;
  retry: () => void;
}) {
  return (
    <div className="knowledge-loading knowledge-loading--error" role="alert">
      <div className="error-icon">!</div>
      <strong>No se pudo cargar el detalle</strong>
      <p>{message}</p>
      <button onClick={retry}>Reintentar</button>
    </div>
  );
}
