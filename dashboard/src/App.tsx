import { useEffect, useState } from 'react'
import { Activity, BarChart3, BookOpen, ChartNoAxesCombined, CloudCog, FlaskConical, LayoutDashboard, Menu, ScrollText, ServerCog, ShieldCheck, X } from 'lucide-react'
import { CollectorHealth } from './pages/CollectorHealth'
import { LogsEvents } from './pages/LogsEvents'
import { Overview } from './pages/Overview'
import { PlaceholderPage } from './pages/PlaceholderPage'
import { SafetyCenter } from './pages/SafetyCenter'
import './index.css'

const primary = [
  { id: 'overview', label: '개요', icon: LayoutDashboard },
  { id: 'collector', label: '수집기 상태', icon: Activity },
  { id: 'safety', label: '안전 센터', icon: ShieldCheck },
  { id: 'logs', label: '로그 / 이벤트', icon: ScrollText },
]
const planned = [
  { id: 'trading', label: '트레이딩', icon: ChartNoAxesCombined },
  { id: 'performance', label: '성과', icon: BarChart3 },
  { id: 'research', label: '연구실', icon: FlaskConical },
  { id: 'infrastructure', label: 'AWS / 인프라', icon: CloudCog },
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
  const content = page === 'overview' ? <Overview /> : page === 'collector' ? <CollectorHealth /> : page === 'safety' ? <SafetyCenter /> : page === 'logs' ? <LogsEvents /> : <PlaceholderPage title={planned.find((item) => item.id === page)?.label ?? '모듈'} description={page === 'trading' ? '실행 화면과 주문 상태는 안전 게이트 뒤에 유지됩니다.' : page === 'performance' ? '검증된 포트폴리오와 손익 근거가 여기에 표시됩니다.' : page === 'research' ? '재현 가능한 실험과 승격 기록이 여기에 표시됩니다.' : '클라우드 자원과 서비스 지표가 여기에 표시됩니다.'} />
  return (
    <div className="app-shell">
      <aside className={`sidebar${menuOpen ? ' open' : ''}`}>
        <div className="brand"><span className="brand-mark"><ServerCog size={20} /></span><div><strong>퀀트 운영</strong><small>통제 화면</small></div><button className="icon-button mobile-only" onClick={() => setMenuOpen(false)} aria-label="탐색 메뉴 닫기"><X size={18} /></button></div>
        <nav aria-label="주요 탐색 메뉴"><span className="nav-section">운영</span>{primary.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={16} /><span>{label}</span></button>)}<span className="nav-section">예정</span>{planned.map(({ id, label, icon: Icon }) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={16} /><span>{label}</span><small>추후</small></button>)}</nav>
        <div className="sidebar-footer"><div><span className="pulse-dot" /><strong>연구 모드</strong></div><small>실거래 실행 없음</small></div>
      </aside>
      {menuOpen && <button className="backdrop" aria-label="탐색 메뉴 오버레이 닫기" onClick={() => setMenuOpen(false)} />}
      <div className="workspace">
        <header className="topbar"><button className="icon-button mobile-only" onClick={() => setMenuOpen(true)} aria-label="탐색 메뉴 열기"><Menu size={19} /></button><div className="environment"><BookOpen size={15} /><strong>모의 데이터 / 개발환경</strong><span>정적 fixture · backend 없음</span></div><div className="topbar-state"><span className="pulse-dot" />읽기 전용</div></header>
        <main>{content}</main>
        <footer className="app-footer"><span>퀀트 대시보드 UI v0.1</span><span>모의 스냅샷 · 2026-08-29 KST</span></footer>
      </div>
    </div>
  )
}

export default App
