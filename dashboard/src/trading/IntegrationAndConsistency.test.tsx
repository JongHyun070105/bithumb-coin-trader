import type { ReactElement } from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { createDemoSnapshot } from './demo'
import type { TradingSnapshot } from './model'

function navigationButton(name: string) {
  const nav = screen.getByRole('navigation', { name: '기본 내비게이션' })
  return within(nav).getByRole('button', { name })
}

describe('Post-72H Dashboard Integration & Consistency', () => {
  beforeEach(() => {
    window.location.hash = ''
  })

  afterEach(() => {
    cleanup()
    window.location.hash = ''
  })

  it('verifies cross-page consistency on identical snapshot data', async () => {
    const user = userEvent.setup()
    render(<App />)

    // Activate Demo Data
    await user.click(screen.getByRole('button', { name: '데모 미리보기' }))

    // 1. Dashboard Page
    const dashboardEquity = screen.getByRole('article', { name: '포트폴리오 가치' })
    expect(dashboardEquity).toHaveTextContent('₩10,843,200')

    const dashboardTotalPnl = screen.getByRole('article', { name: '총 PnL' })
    expect(dashboardTotalPnl).toHaveTextContent('+₩843,200')

    // 2. Navigate to Portfolio Page
    await user.click(navigationButton('포트폴리오'))
    const portfolioEquity = screen.getByRole('article', { name: '총 자본' })
    expect(portfolioEquity).toHaveTextContent('₩10,843,200')

    // 3. Navigate to Positions Page
    await user.click(navigationButton('보유 포지션'))
    const openPositionsCount = screen.getByRole('article', { name: '오픈 포지션' })
    expect(openPositionsCount).toHaveTextContent('3')

    // 4. Navigate to Trades Page
    await user.click(navigationButton('거래 내역'))
    const executedTradesCount = screen.getByRole('article', { name: '체결 거래' })
    expect(executedTradesCount).toHaveTextContent('12')

    // 5. Navigate to Performance Page
    await user.click(navigationButton('성과 분석'))
    const performanceNetPnl = screen.getByRole('article', { name: '순 PnL' })
    expect(performanceNetPnl).toHaveTextContent('+₩843,200')
  })

  it('renders stale warning badge when snapshot is marked as stale', () => {
    const staleSnapshot: TradingSnapshot = {
      ...createDemoSnapshot(),
      source: { kind: 'authoritative', label: 'Authoritative Stream' },
      timestamp: '2026-09-08T00:00:00.000Z',
    }
    render(<App initialTradingState={{ status: 'REAL_DATA', snapshot: staleSnapshot, isStale: true }} />)

    expect(screen.getByText(/데이터가 오래되었습니다/)).toBeInTheDocument()
    expect(screen.getByText(/마지막 업데이트/)).toBeInTheDocument()
  })

  it('renders local snapshot badge when loaded as LOCAL_SNAPSHOT', () => {
    const snapshot = createDemoSnapshot()
    render(<App initialTradingState={{ status: 'LOCAL_SNAPSHOT', snapshot }} />)

    expect(screen.getAllByText('로컬 스냅샷').length).toBeGreaterThan(0)
    expect(screen.getByRole('article', { name: '포트폴리오 가치' })).toHaveTextContent('₩10,843,200')
  })

  it('ErrorBoundary gracefully catches rendering errors with Korean guidance', () => {
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    function BombComponent(): ReactElement {
      throw new Error('Test explosion')
    }

    render(
      <ErrorBoundary>
        <BombComponent />
      </ErrorBoundary>
    )

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('화면을 표시하는 중 문제가 발생했습니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /대시보드로 돌아가기/ })).toBeInTheDocument()

    consoleSpy.mockRestore()
  })

  it('ensures absolute absence of private keys and trade execution buttons', () => {
    render(<App />)

    // No buy/sell/order buttons
    expect(screen.queryByRole('button', { name: /주문|매수|매도|buy|sell|order/i })).not.toBeInTheDocument()
    // No API key fields
    expect(screen.queryByRole('textbox', { name: /api|key|secret/i })).not.toBeInTheDocument()
    // Read only badges
    expect(screen.getAllByText(/페이퍼 OFF/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/라이브 비활성화/).length).toBeGreaterThan(0)
  })
})
