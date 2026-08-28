import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import App from './App'

describe('Quant operations dashboard', () => {
  beforeEach(() => { window.history.replaceState(null, '', '#overview') })
  afterEach(() => cleanup())

  it('identifies mock mode and does not fabricate unavailable PnL', () => {
    render(<App />)
    expect(screen.getByText('모의 데이터 / 개발환경')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '개요' })).toBeInTheDocument()
    const performance = screen.getByText('트레이딩 성과').closest('article')
    expect(performance).not.toBeNull()
    expect(within(performance as HTMLElement).getByText('제공 안 됨')).toBeInTheDocument()
    expect(screen.getByText('아니오')).toBeInTheDocument()
  })

  it('shows the retained Binance integrity finding in collector health', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '수집기 상태' }))
    expect(screen.getByRole('heading', { name: '수집기 상태' })).toBeInTheDocument()
    expect(screen.getByText(/시장 symbol이 UNKNOWN/)).toBeInTheDocument()
    expect(screen.getAllByText('검증 불가').length).toBeGreaterThan(0)
  })

  it('keeps live controls disabled and exposes no switch', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '안전 센터' }))
    expect(screen.getByText('실거래 비활성')).toBeInTheDocument()
    expect(screen.getByText('읽기 전용 화면입니다. 안전 설정을 우회하는 제어는 구현하지 않았습니다.')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('filters the event timeline and renders planned placeholders', async () => {
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: '로그 / 이벤트' }))
    await user.click(screen.getByRole('button', { name: '데이터' }))
    expect(screen.getByText('바이낸스 호가 symbol 미확인')).toBeInTheDocument()
    expect(screen.queryByText('트레이딩 제어 비활성 유지')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /AWS \/ 인프라/ }))
    expect(screen.getByRole('heading', { name: '추후 제공' })).toBeInTheDocument()
  })
})
