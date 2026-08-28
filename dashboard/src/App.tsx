import { useEffect, useState } from 'react'
import { Activity, BarChart3, BookOpen, ChartNoAxesCombined, CloudCog, FlaskConical, LayoutDashboard, Menu, ScrollText, ServerCog, ShieldCheck, X } from 'lucide-react'
import { CollectorHealth } from './pages/CollectorHealth'
import { LogsEvents } from './pages/LogsEvents'
import { Overview } from './pages/Overview'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { SafetyCenter } from './pages/SafetyCenter'
import './index.css'

const primary = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'collector', label: 'Collector Health', icon: Activity },
  { id: 'safety', label: 'Safety Center', icon: ShieldCheck },
  { id: 'logs', label: 'Logs / Events', icon: ScrollText },
]
const planned = [
  { id: 'trading', label: 'Trading', icon: ChartNoAxesCombined },
  { id: 'performance', label: 'Performance', icon: BarChart3 },
  { id: 'research', label: 'Research Lab', icon: FlaskConical },
  { id: 'infrastructure', label: 'AWS / Infrastructure', icon: CloudCog },
]

function pageFromHash() {
  const id = window.location.hash.slice(1)
  return [...primary, ...planned].some((item) => item.id === id) ? id : 'overview'
}

function App() {
  const [page, setPage] = useState(pageFromHash)
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => { const onHash = () => setPage(pageFromHash()); window.addEventListener('hashchange', onHash); return () => window.removeEventListener('hashchange', onHash) }, [])
  const navigate = (id: string) => { window.history.replaceState(null, '', `#${id}`); setPage(id); setMenuOpen(false) }
  const content = page === 'overview' ? <Overview /> : page === 'collector' ? <CollectorHealth /> : page === 'safety' ? <SafetyCenter /> : page === 'logs' ? <LogsEvents /> : <PlaceholderPage title={planned.find((item) => item.id === page)?.label ?? 'Module'} description={page === 'trading' ? 'Execution views and order state will remain gated.' : page === 'performance' ? 'Validated portfolio and PnL evidence will appear here.' : page === 'research' ? 'Reproducible experiments and promotion records will appear here.' : 'Cloud resources and service metrics will appear here.'} />
  return (
    <div className="app-shell">
      <aside className={`sidebar${menuOpen ? ' open' : ''}`}>
        <div className="brand"><span className="brand-mark"><ServerCog size={20} /></span><div><strong>QUANT OPS</strong><small>CONTROL PLANE</small></div><button className="icon-button mobile-only" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><X size={18} /></button></div>
        <nav aria-label="Primary navigation"><span className="nav-section">OPERATIONS</span>{primary.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={16} /><span>{label}</span></button>)}<span className="nav-section">PLANNED</span>{planned.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={16} /><span>{label}</span><small>LATER</small></button>)}</nav>
        <div className="sidebar-footer"><div><span className="pulse-dot" /><strong>RESEARCH MODE</strong></div><small>NO LIVE EXECUTION</small></div>
      </aside>
      {menuOpen && <button className="backdrop" aria-label="Close navigation overlay" onClick={() => setMenuOpen(false)} />}
      <div className="workspace">
        <header className="topbar"><button className="icon-button mobile-only" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Menu size={19} /></button><div className="environment"><BookOpen size={15} /><strong>MOCK DATA / DEVELOPMENT</strong><span>Static fixtures · no backend</span></div><div className="topbar-state"><span className="pulse-dot" />READ ONLY</div></header>
        <main>{content}</main>
        <footer className="app-footer"><span>Quant Dashboard UI v0.1</span><span>Mock snapshot · 2026-08-28 KST</span></footer>
      </div>
    </div>
  )
}

export default App
