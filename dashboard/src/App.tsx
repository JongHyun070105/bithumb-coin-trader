import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowUpRight, ChartNoAxesCombined, ChevronRight, CircleHelp, Cpu, FlaskConical, Layers3, LayoutDashboard, LockKeyhole, Menu, ReceiptText, Wallet, X } from 'lucide-react'
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
import { TradingPage } from './trading/TradingPages'
import { createDemoSnapshot } from './trading/demo'
import type { TradingDataState } from './trading/model'
import { prepareTradingState } from './trading/format'
import './index.css'
import './trading/trading.css'

const primary = [
  { id: 'dashboard', label: '대시보드', icon: LayoutDashboard },
  { id: 'portfolio', label: '포트폴리오', icon: Wallet },
  { id: 'positions', label: '보유 포지션', icon: Layers3 },
  { id: 'trades', label: '거래 내역', icon: ReceiptText },
  { id: 'performance', label: '성과 분석', icon: ChartNoAxesCombined },
]
const secondary = [
  { id: 'bot-status', label: '봇 상태', icon: Activity },
  { id: 'system', label: '시스템', icon: Cpu },
]
const advanced = [
  { id: 'overview', label: '개요', component: Overview },
  { id: 'soak72h', label: '72H 무인 수집', component: Soak72h },
  { id: 'evidenceChain', label: '증거 사슬', component: EvidenceChain },
  { id: 'dataQuality', label: '데이터 품질', component: DataQuality },
  { id: 'dataset', label: '연구 데이터셋', component: Dataset },
  { id: 'researchLab', label: '연구실', component: ResearchLab },
  { id: 'infrastructure', label: '인프라 토폴로지', component: Infrastructure },
  { id: 'events', label: '이벤트 타임라인', component: Events },
  { id: 'safetyCenter', label: '안전 센터', component: SafetyCenter },
  { id: 'trading', label: '트레이딩 통제', component: Trading },
]

function pageFromHash() {
  const hash = window.location.hash.slice(1)
  if (advanced.some(item => item.id === hash)) return `advanced/${hash}`
  if ([...primary, ...secondary].some(item => item.id === hash)) return hash
  if (hash.startsWith('advanced/') && advanced.some(item => item.id === hash.split('/')[1])) return hash
  return 'dashboard'
}

function AdvancedArea({ page, navigate }: { page: string; navigate: (page: string) => void }) {
  const { mode, artifacts } = useEvidence()
  const selected = advanced.find(item => item.id === page.split('/')[1]) ?? advanced[0]
  const Content = selected.component
  return <section className="advanced-area" aria-label="연구 및 증거">
    <div className="trade-page-heading"><div><span className="eyebrow">고급 기능</span><h1>연구 &amp; 증거</h1><p>오프라인 진단 및 로컬 증거. 트레이딩 상태는 독립적으로 유지됩니다.</p></div>
      <div className="advanced-source"><strong>{mode === 'SYNTHETIC_DEMO' ? 'SYNTHETIC DEMO' : mode === 'IMPORTED_EVIDENCE' ? '증거 로드됨' : 'NO EVIDENCE'}</strong><small>{artifacts.length}개 아티팩트 로드됨</small></div>
    </div>
    <nav className="advanced-tabs" aria-label="연구 및 증거 페이지">{advanced.map(item => <button key={item.id} aria-current={selected.id === item.id ? 'page' : undefined} onClick={() => navigate(`advanced/${item.id}`)}>{item.label}</button>)}</nav>
    <Content />
  </section>
}

function DashboardShell({ initialTradingState }: { initialTradingState?: TradingDataState }) {
  const [page, setPage] = useState(pageFromHash)
  const [menuOpen, setMenuOpen] = useState(false)
  const [data, setData] = useState<TradingDataState>(() => prepareTradingState(initialTradingState ?? { status: 'NO_DATA' }))
  const menuButton = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLElement>(null)
  const isAdvanced = page.startsWith('advanced/')
  const isDemo = data.status === 'SYNTHETIC_DEMO'

  useEffect(() => {
    const onHash = () => { setPage(pageFromHash()); setMenuOpen(false) }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (!menuOpen) return
    menu.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setMenuOpen(false); menuButton.current?.focus() }
      if (event.key !== 'Tab') return
      const buttons = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button') ?? []).filter(button => button.getClientRects().length > 0)
      const first = buttons[0], last = buttons.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', keyboard)
    return () => document.removeEventListener('keydown', keyboard)
  }, [menuOpen])

  const navigate = (id: string) => {
    window.history.pushState(null, '', `#${id}`)
    setPage(id)
    if (menuOpen) menuButton.current?.focus()
    setMenuOpen(false)
  }
  const closeMenu = () => { setMenuOpen(false); menuButton.current?.focus() }

  return <div className="trade-shell">
    <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
    <aside id="trading-navigation" ref={menu} className={`trade-sidebar${menuOpen ? ' is-open' : ''}`} aria-label="기본 내비게이션">
      <div className="trade-brand"><span className="trade-brand-mark"><ChartNoAxesCombined size={21} /></span><span><strong>Bithumb</strong><small>Coin Trader</small></span><button className="trade-icon-button mobile-control" onClick={closeMenu} aria-label="내비게이션 닫기"><X size={20} /></button></div>
      <div className="sidebar-account"><span className="account-avatar">B</span><span>개인 워크스페이스<small>KRW · 현물 시장</small></span></div>
      <nav className="trade-nav" aria-label="기본 내비게이션">{primary.map(({ id, label, icon: Icon }) => <button key={id} aria-current={page === id ? 'page' : undefined} onClick={() => navigate(id)}><Icon size={18} /><span>{label}</span>{page === id && <span className="nav-active-dot" />}</button>)}</nav>
      <nav className="trade-nav secondary-nav" aria-label="운영">{secondary.map(({ id, label, icon: Icon }) => <button key={id} aria-current={page === id ? 'page' : undefined} onClick={() => navigate(id)}><Icon size={18} /><span>{label}</span></button>)}</nav>
      <nav className="trade-nav advanced-nav" aria-label="고급 기능 내비게이션"><button aria-label="고급 기능 — 연구 및 증거" aria-current={isAdvanced ? 'page' : undefined} onClick={() => navigate('advanced/overview')}><FlaskConical size={18} /><span>고급 기능</span><ChevronRight size={15} /></button></nav>
      <div className="trade-sidebar-bottom"><span className="connection-dot" /> 로컬 워크스페이스<small>트레이딩 콘솔 v0.3</small><button onClick={() => navigate('system')}><CircleHelp size={15} /> 워크스페이스 정보 <ArrowUpRight size={13} /></button></div>
    </aside>
    {menuOpen && <button className="trade-backdrop" onClick={closeMenu} aria-label="내비게이션 오버레이 닫기" tabIndex={-1} />}
    <div className="trade-workspace">
      <header className="trade-topbar">
        <div className="topbar-title"><button ref={menuButton} className="trade-icon-button mobile-control" aria-label="내비게이션 열기" aria-expanded={menuOpen} aria-controls="trading-navigation" onClick={() => setMenuOpen(true)}><Menu size={20} /></button><span>Bithumb Coin Trader</span><span className="topbar-divider" /><small>개인 트레이딩 콘솔</small></div>
        <div className="execution-strip" aria-label="실행 상태"><span><i /> 페이퍼 OFF</span><span><LockKeyhole size={12} /> 라이브 비활성화</span></div>
      </header>
      <div className="trade-utility-bar"><div className="market-strip"><span><i /> 시장 데이터 <b>대기 중</b></span><span>전략 <b>미배포</b></span></div><div className="data-mode-control"><span className={isDemo ? 'demo-data-badge' : 'source-label'}>{isDemo ? '데모 데이터' : data.status === 'REAL_DATA' ? '실제 데이터' : '트레이딩 데이터 없음'}</span><button onClick={() => setData(isDemo ? { status: 'NO_DATA' } : { status: 'SYNTHETIC_DEMO', snapshot: createDemoSnapshot() })}>{isDemo ? '데모 종료' : '데모 미리보기'}<ArrowUpRight size={13} /></button></div></div>
      <main id="main-content" className="trade-main" tabIndex={-1}>
        {isAdvanced ? <AdvancedArea page={page} navigate={navigate} /> : <TradingPage key={page} page={page} data={data} navigate={navigate} />}
      </main>
      <footer className="trade-footer"><span>읽기 전용 워크스페이스 <span>·</span> Asia/Seoul (KST)</span><span>{isDemo ? '합성 데이터 미리보기 · 주문이 실행되지 않습니다' : '페이퍼 트레이딩이 시작되지 않았습니다'}</span></footer>
    </div>
  </div>
}

export default function App({ initialTradingState }: { initialTradingState?: TradingDataState }) {
  return <EvidenceProvider><DashboardShell initialTradingState={initialTradingState} /></EvidenceProvider>
}
