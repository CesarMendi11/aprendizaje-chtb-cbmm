import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminApiError, getKnowledgeTree } from "../../api/client";
import type {
  CrawlJobRequest,
  KnowledgeTreeErp,
  KnowledgeTreeModule,
  KnowledgeTreeResponse,
  KnowledgeTreeScreen,
} from "../../types/admin";

type ScreenOption = {
  screen: KnowledgeTreeScreen;
  label: string;
};

const errorMessage = (error: unknown) =>
  error instanceof AdminApiError
    ? error.message
    : "No fue posible cargar los targets canónicos del crawler.";

const moduleLabel = (module: KnowledgeTreeModule) =>
  module.navigation_path.length
    ? module.navigation_path.join(" › ")
    : (module.name ?? module.module_id);

const orderedModules = (erp: KnowledgeTreeErp | null) =>
  [...(erp?.modules ?? [])]
    .filter((module) => module.available)
    .sort((left, right) => {
      const depth = left.depth - right.depth;
      return depth || moduleLabel(left).localeCompare(moduleLabel(right), "es");
    });

const screenOptions = (erp: KnowledgeTreeErp | null): ScreenOption[] => {
  if (!erp) return [];
  const options: ScreenOption[] = [];
  for (const module of orderedModules(erp)) {
    const prefix = moduleLabel(module);
    for (const screen of module.screens) {
      if (!screen.route || !screen.structural_available) continue;
      options.push({
        screen,
        label: `${prefix} › ${screen.title ?? screen.route}`,
      });
    }
  }
  for (const screen of erp.unassigned_screens) {
    if (!screen.route || !screen.structural_available) continue;
    options.push({
      screen,
      label: `Sin módulo › ${screen.title ?? screen.route}`,
    });
  }
  return options.sort((left, right) =>
    left.label.localeCompare(right.label, "es"),
  );
};

export function CrawlTargetPicker({
  disabled,
  onLaunch,
}: {
  disabled: boolean;
  onLaunch: (payload: CrawlJobRequest) => void;
}) {
  const [tree, setTree] = useState<KnowledgeTreeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [erpId, setErpId] = useState("");
  const [moduleId, setModuleId] = useState("");
  const [screenId, setScreenId] = useState("");

  const loadTargets = useCallback(async () => {
    setLoading(true);
    setMessage(null);
    try {
      const response = await getKnowledgeTree({ includeEmptyModules: true });
      setTree(response);
      setErpId((current) =>
        response.erps.some((erp) => erp.erp_id === current)
          ? current
          : (response.erps[0]?.erp_id ?? ""),
      );
    } catch (error: unknown) {
      setMessage(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTargets();
  }, [loadTargets]);

  const erp = useMemo(
    () =>
      tree?.erps.find((candidate) => candidate.erp_id === erpId) ??
      tree?.erps[0] ??
      null,
    [erpId, tree],
  );
  const modules = useMemo(() => orderedModules(erp), [erp]);
  const screens = useMemo(() => screenOptions(erp), [erp]);

  useEffect(() => {
    if (moduleId && !modules.some((module) => module.module_id === moduleId))
      setModuleId("");
    if (
      screenId &&
      !screens.some((option) => option.screen.screen_id === screenId)
    )
      setScreenId("");
  }, [moduleId, modules, screenId, screens]);

  const selectedScreen =
    screens.find((option) => option.screen.screen_id === screenId)?.screen ??
    null;
  const partialDisabled = disabled || loading || !erp;

  const launchModule = () => {
    if (!erp || !moduleId || partialDisabled) return;
    onLaunch({
      scope: "module",
      target_module_id: moduleId,
      knowledge_version_id: erp.active_knowledge_version_id,
      headless: false,
      slow_mo: 100,
    });
  };

  const launchScreen = () => {
    if (!erp || !selectedScreen?.route || partialDisabled) return;
    onLaunch({
      scope: "screen",
      target: selectedScreen.route,
      knowledge_version_id: erp.active_knowledge_version_id,
      headless: false,
      slow_mo: 100,
    });
  };

  return (
    <article
      className="pipeline-crawl-targets"
      aria-label="Recorridos parciales gobernados"
    >
      <div className="pipeline-crawl-targets__head">
        <div>
          <span className="pipeline-eyebrow">Recorrido dirigido</span>
          <h3>Seleccionar target desde PostgreSQL ACTIVE</h3>
          <p>
            MODULE y SCREEN se fijan a la versión activa seleccionada. El
            frontend no acepta rutas o IDs escritos manualmente.
          </p>
        </div>
        <button
          className="pipeline-refresh"
          onClick={() => void loadTargets()}
          disabled={loading}
        >
          {loading ? "Cargando targets…" : "Actualizar targets"}
        </button>
      </div>

      {message && (
        <div className="pipeline-error" role="alert">
          {message}
        </div>
      )}
      {!loading && !erp && (
        <div className="pipeline-notice">
          No existe una versión ACTIVE con módulos/pantallas publicables.
          Ejecuta un FULL bootstrap y promoción antes de usar crawls parciales.
        </div>
      )}

      {erp && (
        <>
          <div className="pipeline-target-context">
            {tree && tree.erps.length > 1 && (
              <label>
                <span>ERP</span>
                <select
                  value={erp.erp_id}
                  onChange={(event) => {
                    setErpId(event.target.value);
                    setModuleId("");
                    setScreenId("");
                  }}
                  disabled={partialDisabled}
                >
                  {tree.erps.map((candidate) => (
                    <option key={candidate.erp_id} value={candidate.erp_id}>
                      {candidate.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div>
              <span>ERP</span>
              <strong>{erp.name}</strong>
            </div>
            <div>
              <span>ACTIVE</span>
              <code>{erp.knowledge_version}</code>
            </div>
          </div>

          <div className="pipeline-target-grid">
            <div className="pipeline-target-selector">
              <label htmlFor="pipeline-module-target">Módulo / submódulo</label>
              <select
                id="pipeline-module-target"
                value={moduleId}
                onChange={(event) => setModuleId(event.target.value)}
                disabled={partialDisabled || modules.length === 0}
              >
                <option value="">Selecciona un módulo</option>
                {modules.map((module) => (
                  <option key={module.module_id} value={module.module_id}>
                    {moduleLabel(module)}
                  </option>
                ))}
              </select>
              <small>{modules.length} módulos publicables disponibles.</small>
              <button
                onClick={launchModule}
                disabled={partialDisabled || !moduleId}
              >
                Recorrer módulo
              </button>
            </div>

            <div className="pipeline-target-selector">
              <label htmlFor="pipeline-screen-target">Pantalla</label>
              <select
                id="pipeline-screen-target"
                value={screenId}
                onChange={(event) => setScreenId(event.target.value)}
                disabled={partialDisabled || screens.length === 0}
              >
                <option value="">Selecciona una pantalla</option>
                {screens.map((option) => (
                  <option
                    key={option.screen.screen_id}
                    value={option.screen.screen_id}
                  >
                    {option.label}
                  </option>
                ))}
              </select>
              <small>
                {selectedScreen?.route ??
                  `${screens.length} pantallas con ruta canónica disponibles.`}
              </small>
              <button
                className="pipeline-primary"
                onClick={launchScreen}
                disabled={partialDisabled || !selectedScreen?.route}
              >
                Recorrer pantalla
              </button>
            </div>
          </div>
        </>
      )}
    </article>
  );
}
