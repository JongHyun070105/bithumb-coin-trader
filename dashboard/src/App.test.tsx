import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import App from './App'

describe('Quant operations dashboard', () => {
  beforeEach(() => { window.history.replaceState(null, '', '#overview') })
  afterEach(() => cleanup())

  it('identifies mock mode and does not fabricate unavailable PnL', () => {
    render(<App />)
    expect(screen.getByText('MOCK DATA / DEVELOPMENT')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Overview' })).toBeInTheDocument()
    const performance = screen.getByText('Trading performance').closest('article')
    expect(performance).not.toBeNull()
    expect(within(performance as HTMLElement).getByText('NOT AVAILABLE')).toBeInTheDocument()
    expect(screen.getByText('FALSE')).toBeInTheDocument()
  })

  it('shows the retained Binance integrity finding in collector health', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Collector Health' }))
    expect(screen.getByRole('heading', { name: 'Collector Health' })).toBeInTheDocument()
    expect(screen.getByText(/Market symbol is recorded as UNKNOWN/)).toBeInTheDocument()
    expect(screen.getAllByText('NOT VERIFIABLE').length).toBeGreaterThan(0)
  })

  it('keeps live controls disabled and exposes no switch', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Safety Center' }))
    expect(screen.getByText('LIVE TRADING DISABLED')).toBeInTheDocument()
    expect(screen.getByText('Read-only surface. No safety override controls are implemented.')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('filters the event timeline and renders planned placeholders', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Logs / Events' }))
    await user.click(screen.getByRole('button', { name: 'DATA' }))
    expect(screen.getByText('Binance orderbook symbol unresolved')).toBeInTheDocument()
    expect(screen.queryByText('Trading controls remain disabled')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /AWS \/ Infrastructure/ }))
    expect(screen.getByRole('heading', { name: 'Coming later' })).toBeInTheDocument()
  })
})
