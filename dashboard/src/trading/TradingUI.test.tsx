import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import App from '../App'
import { createDemoSnapshot } from './demo'
import type { TradingSnapshot } from './model'

const tradingPages = ['대시보드', '포트폴리오', '보유 포지션', '거래 내역', '성과 분석']
const operationsPages = ['봇 상태', '시스템']

function navigationButton(name: string) {
  const navigation = operationsPages.includes(name) ? '운영' : '기본 내비게이션'
  return within(screen.getByRole('navigation', { name: navigation })).getByRole('button', { name })
}

function metric(name: string) {
  return screen.getByRole('article', { name })
}

function expectUnknownMetric(name: string) {
  expect(metric(name)).toHaveTextContent('—')
  expect(metric(name)).not.toHaveTextContent(/₩\s*0(?:\D|$)|\b0(?:\.0+)?%/)
}

function expectNoExecutionControls() {
  expect(screen.queryByRole('button', { name: /\b(buy|sell|unlock|submit order|start paper|enable live)\b|매수|매도|봉인 해제/i })).not.toBeInTheDocument()
  expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  expect(screen.queryByRole('textbox', { name: /api|secret|key|credential/i })).not.toBeInTheDocument()
  expect(document.querySelector('input[type="password"]')).toBeNull()
  expect(screen.getByText('페이퍼 OFF', { exact: true })).toBeInTheDocument()
  expect(screen.getByText('라이브 비활성화', { exact: true })).toBeInTheDocument()
}

/** Deliberately independent of the trading preview factory. */
function suppliedSnapshot(): TradingSnapshot {
  return {
    timestamp: '2026-09-08T07:30:00.000Z',
    mode: 'OFF',
    source: { kind: 'authoritative', label: 'Local test adapter' },
    portfolio: {
      equity: 1_234_567, cash: null, exposure: null,
      // Cached metrics must not bypass the daily-baseline requirement.
      todayPnl: 987_654, todayReturnPct: 87.65,
      totalPnl: null, totalReturnPct: null,
      realizedPnl: null, unrealizedPnl: null, fees: null,
    },
    positions: [], recentTrades: [], equityCurve: [], dailyPerformance: [],
    botStatus: {
      mode: 'OFF', strategy: null, marketData: 'PENDING',
      orderExecution: 'DISABLED', riskGuard: 'LOCKED',
      lastActivity: null, uptimeSeconds: null, todayTrades: null, errors: null,
    },
    performance: {
      return7d: null, return30d: null, totalReturn: null,
      maxDrawdown: null, winRate: null, profitFactor: null, averageTrade: null,
    },
    today: {
      realizedPnl: null, unrealizedPnl: null, fees: null,
      trades: null, wins: null, losses: null, exposurePct: null,
    },
    dailyBaseline: null,
  }
}

describe('Trading-first dashboard', () => {
  beforeEach(() => window.history.replaceState(null, '', '#dashboard'))
  afterEach(cleanup)

  it('opens the trading dashboard with unknown values and useful empty states', () => {
    render(<App />)

    expect(navigationButton('대시보드')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('region', { name: '대시보드 페이지' })).toBeInTheDocument()
    for (const name of ['포트폴리오 가치', '오늘 PnL', '오늘 수익률', '총 PnL']) expectUnknownMetric(name)
    expect(screen.getAllByText('트레이딩 데이터 없음').length).toBeGreaterThan(0)
    expect(screen.getByText('아직 트레이딩 내역이 없습니다', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('오픈 포지션 없음', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('거래 내역이 없습니다', { exact: true })).toBeInTheDocument()
    expect(screen.queryByText('데모 데이터', { exact: true })).not.toBeInTheDocument()
  })

  it('keeps research detail out of every primary page and execution controls out of all everyday pages', async () => {
    const user = userEvent.setup()
    render(<App />)

    expect(within(screen.getByRole('navigation', { name: '기본 내비게이션' })).getAllByRole('button').map(button => button.textContent)).toEqual(tradingPages)
    for (const name of [...tradingPages, ...operationsPages]) {
      await user.click(navigationButton(name))
      const content = screen.getByRole('region', { name: `${name} 페이지` })
      expect(content).not.toHaveTextContent(/dataset_id|source_epoch_id|source_run_id|runtime fingerprint|manifest_sha256|dq_qualification_sha256|\bDSR\b|\bPBO\b|\bWRC\b/)
      expect(screen.queryByRole('navigation', { name: '연구 및 증거 페이지' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'LOAD SYNTHETIC DEMO' })).not.toBeInTheDocument()
      expectNoExecutionControls()
    }
  })

  it('reveals the retained evidence workspace only after opening Advanced', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '고급 기능 — 연구 및 증거' }))

    const researchNavigation = screen.getByRole('navigation', { name: '연구 및 증거 페이지' })
    for (const name of ['개요', '72H 무인 수집', '증거 사슬', '데이터 품질', '연구 데이터셋', '연구실', '인프라 토폴로지']) {
      expect(within(researchNavigation).getByRole('button', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'LOAD SYNTHETIC DEMO' })).toBeInTheDocument()
    expect(screen.getByText('NO EVIDENCE', { exact: true })).toBeInTheDocument()
  })

  it('requires explicit preview, keeps its label across routes, and clears all trading values on exit', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '데모 미리보기' }))
    expect(metric('포트폴리오 가치')).toHaveTextContent('₩')
    expect(metric('오늘 수익률')).toHaveTextContent('%')

    for (const name of [...tradingPages, ...operationsPages]) {
      await user.click(navigationButton(name))
      expect(screen.getByText('데모 데이터', { exact: true })).toBeInTheDocument()
      expectNoExecutionControls()
    }
    await user.click(screen.getByRole('button', { name: '데모 종료' }))
    await user.click(navigationButton('대시보드'))
    for (const name of ['포트폴리오 가치', '오늘 PnL', '오늘 수익률', '총 PnL']) expectUnknownMetric(name)
    expect(screen.getByText('오픈 포지션 없음', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('거래 내역이 없습니다', { exact: true })).toBeInTheDocument()
    expect(screen.queryByText('데모 데이터', { exact: true })).not.toBeInTheDocument()
  })

  it('never turns evidence demo artifacts into trading data', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '고급 기능 — 연구 및 증거' }))
    await user.click(screen.getByRole('button', { name: 'LOAD SYNTHETIC DEMO' }))
    expect(await screen.findByText('SYNTHETIC DEMO', { exact: true })).toBeInTheDocument()

    await user.click(navigationButton('대시보드'))
    expect(screen.getAllByText('트레이딩 데이터 없음').length).toBeGreaterThan(0)
    expectUnknownMetric('포트폴리오 가치')
    expectUnknownMetric('오늘 PnL')
    expect(screen.getByText('거래 내역이 없습니다', { exact: true })).toBeInTheDocument()
  })

  it('never turns trading preview into imported or synthetic evidence', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '데모 미리보기' }))
    await user.click(screen.getByRole('button', { name: '고급 기능 — 연구 및 증거' }))

    expect(screen.getByText('데모 데이터', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('NO EVIDENCE', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('0개 아티팩트 로드됨')).toBeInTheDocument()
    expect(screen.queryByText('EVIDENCE LOADED', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('SYNTHETIC DEMO', { exact: true })).not.toBeInTheDocument()
  })

  it('shows loading feedback without substituting zero or preview values', () => {
    render(<App initialTradingState={{ status: 'LOADING' }} />)
    expect(screen.getByRole('region', { name: '대시보드 페이지' })).toHaveTextContent(/로딩/i)
    for (const name of ['포트폴리오 가치', '오늘 PnL', '오늘 수익률', '총 PnL']) expectUnknownMetric(name)
    expect(screen.queryByText('데모 데이터', { exact: true })).not.toBeInTheDocument()
  })

  it('shows a failed data state without fabricated balances or returns', () => {
    render(<App initialTradingState={{ status: 'ERROR', error: 'Local snapshot unavailable' }} />)
    expect(screen.getAllByRole('alert').length).toBeGreaterThan(0)
    for (const name of ['포트폴리오 가치', '오늘 PnL', '오늘 수익률', '총 PnL']) expectUnknownMetric(name)
    expect(screen.queryByText('데모 데이터', { exact: true })).not.toBeInTheDocument()
  })

  it('renders supplied values with their source while rejecting cached today metrics without a baseline', () => {
    render(<App initialTradingState={{ status: 'REAL_DATA', snapshot: suppliedSnapshot() }} />)
    expect(metric('포트폴리오 가치')).toHaveTextContent('1,234,567')
    expect(screen.getByText(/Local test adapter/)).toBeInTheDocument()
    expect(screen.getByText('실제 데이터', { exact: true })).toBeInTheDocument()
    expectUnknownMetric('오늘 PnL')
    expectUnknownMetric('오늘 수익률')
    expect(metric('오늘 PnL')).not.toHaveTextContent('987,654')
    expect(metric('오늘 수익률')).not.toHaveTextContent('87.65')
    expect(screen.queryByText('데모 데이터', { exact: true })).not.toBeInTheDocument()
    expect(screen.getByText('오픈 포지션 없음', { exact: true })).toBeInTheDocument()
  })

  it('lets users select every equity timeframe and change between value and return', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '데모 미리보기' }))
    const timeframes = screen.getByRole('group', { name: '자본 기간' })
    for (const name of ['1D', '7D', '30D', '3M', 'ALL']) {
      await user.click(within(timeframes).getByRole('button', { name }))
      expect(within(timeframes).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
      expect(within(timeframes).getAllByRole('button', { pressed: true })).toHaveLength(1)
      expect(screen.getByRole('img', { name: new RegExp(`^포트폴리오 히스토리, ${name}\\.`) })).toBeInTheDocument()
    }
    const display = screen.getByRole('group', { name: '자본 표시' })
    for (const name of ['수익률 %', '금액']) {
      await user.click(within(display).getByRole('button', { name }))
      expect(within(display).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
      expect(within(display).getAllByRole('button', { pressed: true })).toHaveLength(1)
      const chart = screen.getByRole('img', { name: /^포트폴리오 히스토리, ALL\./ })
      expect(chart).toHaveAccessibleName(name === '금액' ? /₩/ : /%/)
      if (name === '수익률 %') expect(chart).not.toHaveAccessibleName(/₩/)
    }
    expect(screen.getByText('데모 데이터', { exact: true })).toBeInTheDocument()
  })

  it('filters trade rows by outcome using net PnL', async () => {
    const user = userEvent.setup()
    const preview = createDemoSnapshot()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '데모 미리보기' }))
    await user.click(navigationButton('거래 내역'))
    const outcomes = screen.getByRole('group', { name: '거래 결과' })
    const periods = screen.getByRole('group', { name: '거래 기간' })
    await user.click(within(periods).getByRole('button', { name: '전체' }))

    for (const [name, expectedCount] of [
      ['수익', preview.recentTrades.filter(trade => trade.pnl !== null && trade.pnl > 0).length],
      ['손실', preview.recentTrades.filter(trade => trade.pnl !== null && trade.pnl < 0).length],
      ['전체', preview.recentTrades.length],
    ] as const) {
      await user.click(within(outcomes).getByRole('button', { name }))
      expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(expectedCount + 1)
    }
  })

  it('filters trades by the snapshot trading day and selected period rather than the wall clock', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '데모 미리보기' }))
    await user.click(navigationButton('거래 내역'))
    const periods = screen.getByRole('group', { name: '거래 기간' })

    // Fixed preview: 5 closes today, 7 in 7D, 12 in 30D, and 12 total.
    for (const [name, expectedCount] of [['오늘', 5], ['7일', 7], ['30일', 12], ['전체', 12]] as const) {
      await user.click(within(periods).getByRole('button', { name }))
      expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(expectedCount + 1)
    }
  })
})

