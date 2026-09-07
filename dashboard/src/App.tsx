import { useEffect, useState } from 'react'
import {
  Activity,
  ChartNoAxesCombined,
  CloudCog,
  Database,
  FlaskConical,
  LayoutDashboard,
  Link2,
  Lock,
  Menu,
  ScrollText,
  ServerCog,
  ShieldCheck,
  X
} from 'lucide-react'
import { EvidenceProvider } from './context/EvidenceContext'
import { useEvidence } from './context/useEvidence'
import { Overview } from './pages/Overview'
import { Soak72h } from './pages/Soak72h'
import { EvidenceChain } from './pages/EvidenceChain'
import { DataQuality } from './pages/DataQuality'
import { Dataset } from './pages/Dataset'
import { ResearchLab } from './pages/ResearchLab'
import { SafetyCenter } from './pages/SafetyCenter'
import { Events } from './pages/Events'
import { Infrastructure } from './pages/Infrastructure'
import { Trading } from './pages/Trading'
import './index.css'

interface NavItem {
  id: string
  label: string
  icon: React.ComponentType<{ size?: number }>
  badge?: string
}

const navItems: NavItem[] = [
  { id: 'overview', label: '개요 (Overview)', icon: LayoutDashboard },
  { id: 'soak72h', label: '72H 무인 수집', icon: Activity },
  { id: 'evidenceChain', label: '증거 사슬 (Chain)', icon: Link2 },
  { id: 'dataQuality', label: '데이터 품질 (DQ)', icon: ShieldCheck },
  { id: 'dataset', label: '연구 데이터셋', icon: Database },
  { id: 'researchLab', label: '연구실 (Research)', icon: FlaskConical },
  { id: 'safetyCenter', label: '안전 센터', icon: Lock },
  { id: 'events', label: '이벤트 타임라인', icon: ScrollText },
  { id: 'infrastructure', label: '인프라 토폴로지', icon: CloudCog },
  { id: 'trading', label: '트레이딩 (잠금)', icon: ChartNoAxesCombined, badge: '잠김' }
]

function pageFromHash(): string {
  const id = window.location.hash.slice(1)
  return navItems.some((item) => item.id === id) ? id : 'overview'
}

function DashboardShell() {
  const [page, setPage] = useState<string>(pageFromHash)
  const [menuOpen, setMenuOpen] = useState(false)
  const { mode, artifacts } = useEvidence()

  useEffect(() => {
    const onHash = () => setPage(pageFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = (id: string) => {
    window.history.replaceState(null, '', `#${id}`)
    setPage(id)
    setMenuOpen(false)
  }

  const renderContent = () => {
    switch (page) {
      case 'overview':
        return <Overview />
      case 'soak72h':
        return <Soak72h />
      case 'evidenceChain':
        return <EvidenceChain />
      case 'dataQuality':
        return <DataQuality />
      case 'dataset':
        return <Dataset />
      case 'researchLab':
        return <ResearchLab />
      case 'safetyCenter':
        return <SafetyCenter />
      case 'events':
        return <Events />
      case 'infrastructure':
        return <Infrastructure />
      case 'trading':
        return <Trading />
      default:
        return <Overview />
    }
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar${menuOpen ? ' open' : ''}`}>
        <div className="brand">
          <span className="brand-mark">
            <ServerCog size={20} />
          </span>
          <div>
            <strong>BITHUMB COIN TRADER</strong>
            <small>증거 및 연구 콘솔 v0.2</small>
          </div>
          <button className="icon-button mobile-only" onClick={() => setMenuOpen(false)} aria-label="탐색 메뉴 닫기">
            <X size={18} />
          </button>
        </div>

        <nav aria-label="주요 탐색 메뉴">
          <span className="nav-section">증거 및 데이터 검증</span>
          {navItems.slice(0, 5).map(({ id, label, icon: Icon, badge }) => (
            <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
              <Icon size={16} />
              <span>{label}</span>
              {badge && <small className="nav-badge">{badge}</small>}
            </button>
          ))}

          <span className="nav-section">연구 및 거버넌스</span>
          {navItems.slice(5).map(({ id, label, icon: Icon, badge }) => (
            <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}>
              <Icon size={16} />
              <span>{label}</span>
              {badge && <small className="nav-badge">{badge}</small>}
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-mode-status">
            <span className={`pulse-dot ${mode === 'SYNTHETIC_DEMO' ? 'dot-synthetic' : mode === 'IMPORTED_EVIDENCE' ? 'dot-imported' : 'dot-neutral'}`} />
            <strong>{mode === 'SYNTHETIC_DEMO' ? 'SYNTHETIC DEMO' : mode === 'IMPORTED_EVIDENCE' ? 'EVIDENCE LOADED' : 'NO EVIDENCE'}</strong>
          </div>
          <small>{artifacts.length}개 아티팩트 로드됨</small>
        </div>
      </aside>

      {menuOpen && <button className="backdrop" aria-label="탐색 메뉴 오버레이 닫기" onClick={() => setMenuOpen(false)} />}

      <div className="workspace">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setMenuOpen(true)} aria-label="탐색 메뉴 열기">
            <Menu size={19} />
          </button>
          <div className="environment">
            <ShieldCheck size={16} className="text-success" />
            <strong>오프라인 연구 콘솔 (Offline Evidence Console)</strong>
            <span>브라우저 로컬 메모리 전용 · 외부 네트워크 통신 0건</span>
          </div>
          <div className="topbar-state">
            <span className="pulse-dot" />
            읽기 전용 / 격리 모드
          </div>
        </header>

        <main>{renderContent()}</main>

        <footer className="app-footer">
          <span>Bithumb Coin Trader — Dashboard v0.2</span>
          <span>오프라인 증거 콘솔 · Web Crypto SHA-256 검증</span>
        </footer>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <EvidenceProvider>
      <DashboardShell />
    </EvidenceProvider>
  )
}
