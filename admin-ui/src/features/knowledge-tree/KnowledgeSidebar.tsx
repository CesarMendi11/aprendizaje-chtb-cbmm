import { useMemo, useState } from 'react'
import type { KnowledgeTreeErp, KnowledgeTreeModule, KnowledgeTreeScreen } from '../../types/admin'
import { EmptyState } from '../../components/EmptyState'
import { StatusBadge } from '../../components/StatusBadge'

type ModuleNode = KnowledgeTreeModule & { children: ModuleNode[] }

const screenMatches = (screen: KnowledgeTreeScreen, needle: string) => (
  !needle
  || `${screen.title ?? ''} ${screen.route ?? ''}`.toLocaleLowerCase('es').includes(needle)
)

const moduleLabel = (module: KnowledgeTreeModule) => (
  module.navigation_path.length
    ? module.navigation_path.join(' › ')
    : module.name ?? 'Módulo sin nombre'
)

function buildModuleTree(modules: KnowledgeTreeModule[], needle: string): ModuleNode[] {
  const byId = new Map<string, ModuleNode>()
  for (const module of modules) {
    byId.set(module.module_id, {
      ...module,
      screens: module.screens.filter((screen) => screenMatches(screen, needle)),
      children: [],
    })
  }

  const roots: ModuleNode[] = []
  for (const module of byId.values()) {
    const parent = module.parent_module_id ? byId.get(module.parent_module_id) : null
    if (parent) parent.children.push(module)
    else roots.push(module)
  }

  const sortNodes = (nodes: ModuleNode[]) => {
    nodes.sort((left, right) => moduleLabel(left).localeCompare(moduleLabel(right), 'es'))
    for (const node of nodes) sortNodes(node.children)
  }
  sortNodes(roots)

  if (!needle) return roots
  const keepMatches = (nodes: ModuleNode[]): ModuleNode[] => nodes.flatMap((node) => {
    const children = keepMatches(node.children)
    const moduleMatches = moduleLabel(node).toLocaleLowerCase('es').includes(needle)
    if (!moduleMatches && node.screens.length === 0 && children.length === 0) return []
    return [{ ...node, children }]
  })
  return keepMatches(roots)
}

export function KnowledgeSidebar({ erp, selectedId, onSelect }: {
  erp: KnowledgeTreeErp
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const [closed, setClosed] = useState<Set<string>>(new Set())
  const needle = query.trim().toLocaleLowerCase('es')
  const modules = useMemo(() => buildModuleTree(erp.modules, needle), [erp.modules, needle])
  const unassigned = erp.unassigned_screens.filter((screen) => screenMatches(screen, needle))

  const toggle = (id: string) => setClosed((current) => {
    const next = new Set(current)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const screenButton = (screen: KnowledgeTreeScreen) => (
    <button
      key={screen.screen_id}
      className={`screen-link ${selectedId === screen.screen_id ? 'is-selected' : ''}`}
      onClick={() => onSelect(screen.screen_id)}
      aria-current={selectedId === screen.screen_id ? 'page' : undefined}
    >
      <span className="screen-title">{screen.title ?? 'Pantalla sin título'}</span>
      <StatusBadge status={screen.semantic_state} />
    </button>
  )

  const renderModule = (module: ModuleNode) => {
    const expanded = !closed.has(module.module_id)
    const childScreens = module.screens.length
    const childModules = module.children.length
    return <section className="tree-module" key={module.module_id}>
      <button
        className="module-toggle"
        onClick={() => toggle(module.module_id)}
        aria-expanded={expanded}
        title={moduleLabel(module)}
      >
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 5 5 5-5 5"/></svg>
        <span>{module.name ?? 'Módulo sin nombre'}</span>
        <b>{childScreens + childModules}</b>
      </button>
      {expanded && <div className="tree-module__contents">
        {module.screens.length > 0 && <div className="screen-list">{module.screens.map(screenButton)}</div>}
        {module.children.length > 0 && <div className="tree-module__children">
          {module.children.map(renderModule)}
        </div>}
        {module.screens.length === 0 && module.children.length === 0
          && <EmptyState>Este módulo no contiene pantallas ni submódulos publicables.</EmptyState>}
      </div>}
    </section>
  }

  return <aside className="sidebar" aria-label="Jerarquía de conocimiento">
    <div className="sidebar-head">
      <p className="eyebrow">ERP activo</p>
      <h2>{erp.name}</h2>
      <span className="counter">{erp.counters.total_screens} pantallas</span>
    </div>
    <label className="search">
      <span>Buscar pantalla o módulo</span>
      <div>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Título, módulo o ruta" />
      </div>
    </label>
    <nav aria-label="Módulos y pantallas">
      {modules.length === 0 && unassigned.length === 0
        ? <EmptyState>No hay elementos que coincidan con la búsqueda.</EmptyState>
        : modules.map(renderModule)}
      <section className="unassigned">
        <h3>Sin módulo asignado</h3>
        {unassigned.length ? unassigned.map(screenButton) : <EmptyState>No hay pantallas sin asignar.</EmptyState>}
      </section>
    </nav>
  </aside>
}
