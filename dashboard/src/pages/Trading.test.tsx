import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EvidenceProvider } from '../context/EvidenceContext'
import { Trading } from './Trading'

function renderTrading() {
  return render(<EvidenceProvider><Trading /></EvidenceProvider>)
}

describe('Trading PAPER observability', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('does not contact the local API until the operator opts in', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    renderTrading()

    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('no request has been made')
  })

  it('shows a read-only journal snapshot and keeps execution locked', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'AVAILABLE',
        journal_name: 'paper-session.sqlite',
        event_id: 'evt-123',
        timestamp_ms: 1_735_689_600_000,
        metrics_integrity: 'NOT_VERIFIED',
        metrics: {
          market_data_age_ms: 12,
          strategy_state: 'READY',
          signal_count: 3,
          order_count: 2,
          fill_count: 1,
          cash_krw: '900000',
          equity_krw: '1000000',
          risk_state: 'READY',
          journal_state: 'HEALTHY',
          restart_count: 1
        }
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    renderTrading()
    await user.click(screen.getByRole('button', { name: 'Connect local journal' }))

    expect(await screen.findByText('Journal status: AVAILABLE')).toBeInTheDocument()
    expect(screen.getByText(/paper-session.sqlite/)).toBeInTheDocument()
    expect(screen.getByText('evt-123')).toBeInTheDocument()
    expect(screen.getByText(/NOT_VERIFIED/)).toBeInTheDocument()
    expect(screen.getByText('Equity (KRW)')).toBeInTheDocument()
    expect(screen.getByText('1000000')).toBeInTheDocument()
    expect(screen.getByText('PAPER TRADING: NOT STARTED')).toBeInTheDocument()
    expect(screen.getByText('LIVE TRADING: DISABLED')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:8787/api/paper/runtime',
      expect.objectContaining({ method: 'GET' })
    )
    expect(screen.queryByRole('button', { name: /buy|sell|start paper/i })).toBeNull()
  })
})
