import type { ReactNode } from 'react'
import { dataMode } from '../api/client'
import './admin-layout.css'

export type AdminSection = 'overview' | 'structural' | 'semantic' | 'publication' | 'pipeline'

type NavItem = {
  id: AdminSection
  label: string
  description: string
  icon: 'overview' | 'structure' | 'semantic' | 'publication' | 'pipeline'
}

const primaryNav: NavItem[] = [
  { id: 'overview', label: 'Resumen', description: 'Estado general del prototipo', icon: 'overview' },
]

const knowledgeNav: NavItem[] = [
  { id: 'structural', label: 'Estructural', description: 'Pantallas, elementos y revisión', icon: 'structure' },
  { id: 'semantic', label: 'Semántico', description: 'Inferencia y validación HITL', icon: 'semantic' },
]

const operationsNav: NavItem[] = [
  { id: 'publication', label: 'Publicación', description: 'Neo4j y colecciones Chroma', icon: 'publication' },
  { id: 'pipeline', label: 'Pipeline', description: 'Crawler, canonical y jobs', icon: 'pipeline' },
]

function NavIcon({ icon }: { icon: NavItem['icon'] }) {
  if (icon === 'overview') return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg>
  if (icon === 'structure') return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="18" r="2"/><path d="M8 7.2 10.8 16M16 7.2 13.2 16M8 6h8"/></svg>
  if (icon === 'semantic') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5h14v11H9l-4 3V5Z"/><path d="M8 9h8M8 12h5"/></svg>
  if (icon === 'publication') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M8 7l4-4 4 4"/><path d="M5 13v6h14v-6"/></svg>
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M4 12h16M10 17h10"/><circle cx="17" cy="7" r="2"/><circle cx="7" cy="17" r="2"/></svg>
}

function NavButton({ item, active, onSelect }: { item: NavItem; active: boolean; onSelect: (section: AdminSection) => void }) {
  return <button className={`admin-nav__item ${active ? 'is-active' : ''}`} onClick={() => onSelect(item.id)} aria-current={active ? 'page' : undefined}>
    <span className="admin-nav__icon"><NavIcon icon={item.icon} /></span>
    <span className="admin-nav__copy"><strong>{item.label}</strong><small>{item.description}</small></span>
  </button>
}

export function AdminLayout({ activeSection, onNavigate, erpName, knowledgeVersion, sourceStatus, onReload, reloading, children }: {
  activeSection: AdminSection
  onNavigate: (section: AdminSection) => void
  erpName: string
  knowledgeVersion: string | null
  sourceStatus: 'ready' | 'loading' | 'error'
  onReload: () => void
  reloading: boolean
  children: ReactNode
}) {
  return <div className="admin-shell">
    <header className="admin-topbar">
      <div className="admin-brand">
        <div className="admin-brand__mark" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M7 7h18v18H7zM11 12h10M11 16h10M11 20h6"/></svg></div>
        <div><strong>Chat-CBMM</strong><span>Consola de administración del conocimiento</span></div>
      </div>
      <div className="admin-topbar__context">
        <div className="admin-context-chip"><span>ERP</span><strong>{erpName}</strong></div>
        <div className="admin-context-chip admin-context-chip--version"><span>Versión activa</span><code>{knowledgeVersion ?? 'No disponible'}</code></div>
        <span className={`mode mode--${dataMode}`}>{dataMode === 'demo' ? 'Modo demostración' : 'Modo live'}</span>
        <span className={`admin-api-state admin-api-state--${sourceStatus}`}><i />{sourceStatus === 'ready' ? (dataMode === 'demo' ? 'Snapshot validado' : 'API disponible') : sourceStatus === 'error' ? 'API no disponible' : 'Verificando API'}</span>
        <button className="admin-reload" onClick={onReload} disabled={reloading} title="Volver a cargar la jerarquía administrativa"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2 5M20 5v6h-6"/></svg><span>{reloading ? 'Cargando…' : 'Recargar'}</span></button>
      </div>
    </header>

    <div className="admin-frame">
      <aside className="admin-nav" aria-label="Secciones de administración">
        <div className="admin-nav__intro"><span>Administración</span><strong>Gobierno del conocimiento</strong><p>Explora, revisa, publica y supervisa el pipeline sin mezclar responsabilidades.</p></div>
        <nav>
          <div className="admin-nav__group">{primaryNav.map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id} onSelect={onNavigate} />)}</div>
          <div className="admin-nav__group"><span className="admin-nav__group-label">Conocimiento</span>{knowledgeNav.map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id} onSelect={onNavigate} />)}</div>
          <div className="admin-nav__group"><span className="admin-nav__group-label">Operación</span>{operationsNav.map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id} onSelect={onNavigate} />)}</div>
        </nav>
        <div className="admin-nav__footer"><span>Seguridad</span><strong>Identidad provisional</strong><small>RBAC institucional pendiente.</small></div>
      </aside>
      <main className="admin-main">{children}</main>
    </div>
  </div>
}
