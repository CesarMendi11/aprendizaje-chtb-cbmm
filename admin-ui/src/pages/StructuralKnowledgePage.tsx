import { useState } from "react";
import { PageHeader } from "../components/PageHeader";
import { KnowledgeWorkspace } from "../components/KnowledgeWorkspace";
import { RemovalReviewConsole } from "../features/removal-review/RemovalReviewConsole";
import { StructuralReviewConsole } from "../features/structural-review/StructuralReviewConsole";
import { StructuralReviewPackagesConsole } from "../features/structural-review/StructuralReviewPackagesConsole";
import type {
  KnowledgeTreeErp,
  ScreenReviewContextResponse,
} from "../types/admin";
import "./page-shell.css";

type DetailState =
  | { status: "loading"; data?: ScreenReviewContextResponse }
  | { status: "ready"; data: ScreenReviewContextResponse }
  | { status: "error"; message: string; data?: ScreenReviewContextResponse }
  | null;

export function StructuralKnowledgePage({
  erp,
  selectedId,
  detail,
  treeMessage,
  onSelect,
  onRetryTree,
  onRetryDetail,
  onRefresh,
  onOpenJob,
}: {
  erp: KnowledgeTreeErp;
  selectedId: string | null;
  detail: DetailState;
  treeMessage?: string | null;
  onSelect: (id: string) => void;
  onRetryTree: () => void;
  onRetryDetail: () => void;
  onRefresh: () => void | Promise<void>;
  onOpenJob?: (jobId: string) => void;
}) {
  const [view, setView] = useState<
    "explore" | "changes" | "review" | "removals"
  >("explore");
  return (
    <section className="admin-page admin-page--knowledge">
      <PageHeader
        eyebrow="Conocimiento"
        title="Conocimiento estructural"
        description="Explora la topología descubierta del ERP o entra a la cola HITL para aprobar, corregir o rechazar elementos canónicos."
        actions={
          <div
            className="page-segmented"
            role="tablist"
            aria-label="Vista estructural"
          >
            <button
              aria-selected={view === "explore"}
              onClick={() => setView("explore")}
            >
              Explorar ACTIVE
            </button>
            <button
              aria-selected={view === "changes"}
              onClick={() => setView("changes")}
            >
              Cambios del candidate
            </button>
            <button
              aria-selected={view === "review"}
              onClick={() => setView("review")}
            >
              Cola de revisión
            </button>
            <button
              aria-selected={view === "removals"}
              onClick={() => setView("removals")}
            >
              Removals
            </button>
          </div>
        }
      />
      {view === "explore" ? (
        <KnowledgeWorkspace
          erp={erp}
          selectedId={selectedId}
          detail={detail}
          mode="structural"
          treeMessage={treeMessage}
          onSelect={onSelect}
          onRetryTree={onRetryTree}
          onRetryDetail={onRetryDetail}
          onRefresh={onRefresh}
        />
      ) : view === "changes" ? (
        <StructuralReviewPackagesConsole />
      ) : view === "review" ? (
        <StructuralReviewConsole />
      ) : (
        <RemovalReviewConsole onOpenJob={onOpenJob} />
      )}
    </section>
  );
}
