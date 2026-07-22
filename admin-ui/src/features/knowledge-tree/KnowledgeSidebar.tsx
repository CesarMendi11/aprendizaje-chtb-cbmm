import { useMemo, useState } from 'react'
import type { KnowledgeTreeErp, KnowledgeTreeScreen } from '../../types/admin'
import { EmptyState } from '../../components/EmptyState'
import { StatusBadge } from '../../components/StatusBadge'

export function KnowledgeSidebar({ erp, selectedId, onSelect }: { erp: KnowledgeTreeErp; selectedId: string | null; onSelect: (id: string) => void }) {
  const [query, setQuery] = useState('')
  const [closed, setClosed] = useState<Set<string>>(new Set())
  const needle = query.trim().toLocaleLowerCase('es')
  const matches = (screen: KnowledgeTreeScreen) => !needle || `${screen.title ?? ''} ${screen.route ?? ''}`.toLocaleLowerCase('es').includes(needle)
  const modules = useMemo(() => erp.modules.map((module) => ({ ...module, screens: module.screens.filter(matches) })).filter((module) => module.screens.length || !needle), [erp.modules, needle])
  const unassigned = erp.unassigned_screens.filter(matches)
  const toggle = (id: string) => setClosed((current) => { const next = new Set(current); next.has(id) ? next.delete(id) : next.add(id); return next })
  const screenButton = (screen: KnowledgeTreeScreen) => <button key={screen.screen_id} className={`screen-link ${selectedId === screen.screen_id ? 'is-selected' : ''}`} onClick={() => onSelect(screen.screen_id)} aria-current={selectedId === screen.screen_id ? 'page' : undefined}><span className="screen-title">{screen.title ?? 'Pantalla sin título'}</span><StatusBadge status={screen.semantic_state} /></button>
  return <aside className="sidebar" aria-label="Jerarquía de conocimiento">
    <div className="sidebar-head"><p className="eyebrow">ERP activo</p><h2>{erp.name}</h2><span className="counter">{erp.counters.total_screens} pantalla</span></div>
    <label className="search"><span>Buscar pantalla</span><div><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/></svg><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Título o ruta" /></div></label>
    <nav aria-label="Módulos y pantallas">
      {modules.length === 0 && unassigned.length === 0 ? <EmptyState>No hay pantallas que coincidan con la búsqueda.</EmptyState> : modules.map((module) => { const expanded = !closed.has(module.module_id); return <section className="tree-module" key={module.module_id}><button className="module-toggle" onClick={() => toggle(module.module_id)} aria-expanded={expanded}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 5 5 5-5 5"/></svg><span>{module.name ?? 'Módulo sin nombre'}</span><b>{module.screens.length}</b></button>{expanded && <div className="screen-list">{module.screens.length ? module.screens.map(screenButton) : <EmptyState>Este módulo no contiene pantallas.</EmptyState>}</div>}</section> })}
      <section className="unassigned"><h3>Sin módulo asignado</h3>{unassigned.length ? unassigned.map(screenButton) : <EmptyState>No hay pantallas sin asignar.</EmptyState>}</section>
    </nav>
  </aside>
}
