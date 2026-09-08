import { describe, expect, it } from 'vitest'
import { createDemoSnapshot, DEMO_STARTING_CAPITAL, DEMO_TIMESTAMP } from './demo'
import { calculateTodayMetrics, NO_TRADING_DATA, tradingDayInSeoul, type DailyBaseline, type TradingDataState } from './model'

const known = (value: number | null) => {
  expect(value).not.toBeNull()
  expect(Number.isFinite(value)).toBe(true)
  return value as number
}

describe('synthetic trading preview accounting', () => {
  it('is deterministic, unmistakably synthetic, and cannot imply a running bot', () => {
    const demo = createDemoSnapshot()
    expect(demo).toEqual(createDemoSnapshot())
    expect(demo.timestamp).toBe(DEMO_TIMESTAMP)
    expect(demo.source.kind).toBe('synthetic')
    expect(demo.source.label).toContain('DEMO DATA')
    expect(demo.mode).toBe('OFF')
    expect(demo.botStatus).toEqual({
      mode: 'OFF', strategy: null, marketData: 'PENDING', orderExecution: 'DISABLED', riskGuard: 'LOCKED',
      lastActivity: null, uptimeSeconds: null, todayTrades: null, errors: null,
    })
    expect(demo.positions).toHaveLength(3)
    expect(demo.recentTrades).toHaveLength(12)
    expect(demo.dailyPerformance).toHaveLength(30)
  })

  it('reconciles cash, exposure, fee-adjusted positions, and net total PnL', () => {
    const { portfolio, positions, recentTrades } = createDemoSnapshot()
    let entryFees = 0
    for (const position of positions) {
      expect(position.side).toBe('LONG')
      const cost = known(position.entry) * known(position.quantity)
      const exposure = known(position.current) * known(position.quantity)
      expect(position.exposure).toBeCloseTo(exposure, 2)
      expect(position.pnl).toBeCloseTo(exposure - cost - known(position.entryFee), 2)
      expect(position.pnlPct).toBeCloseTo(known(position.pnl) / (cost + known(position.entryFee)) * 100, 8)
      entryFees += known(position.entryFee)
    }
    expect(portfolio.exposure).toBeCloseTo(positions.reduce((sum, position) => sum + known(position.exposure), 0), 2)
    expect(portfolio.unrealizedPnl).toBeCloseTo(positions.reduce((sum, position) => sum + known(position.pnl), 0), 2)
    expect(portfolio.equity).toBeCloseTo(known(portfolio.cash) + known(portfolio.exposure), 2)
    expect(portfolio.totalPnl).toBeCloseTo(known(portfolio.realizedPnl) + known(portfolio.unrealizedPnl), 2)
    expect(portfolio.equity).toBeCloseTo(DEMO_STARTING_CAPITAL + known(portfolio.totalPnl), 2)
    expect(portfolio.totalReturnPct).toBeCloseTo(known(portfolio.totalPnl) / DEMO_STARTING_CAPITAL * 100, 8)
    expect(portfolio.fees).toBeCloseTo(entryFees + recentTrades.reduce((sum, trade) => sum + known(trade.fee), 0), 2)
  })

  it('derives win rate, profit factor, average trade, and realized PnL from the displayed trades', () => {
    const { recentTrades, portfolio, performance } = createDemoSnapshot()
    for (const trade of recentTrades) {
      const gross = (known(trade.exit) - known(trade.entry)) * known(trade.quantity)
      expect(trade.pnl).toBeCloseTo(gross - known(trade.fee), 2)
      expect(Date.parse(trade.closedAt)).toBeGreaterThan(Date.parse(trade.openedAt))
      expect(Date.parse(trade.closedAt)).toBeLessThanOrEqual(Date.parse(DEMO_TIMESTAMP))
      expect(trade.side).toBe('LONG')
    }
    expect(recentTrades.map(trade => trade.closedAt)).toEqual(recentTrades.map(trade => trade.closedAt).sort().reverse())
    const wins = recentTrades.filter(trade => known(trade.pnl) > 0)
    const losses = recentTrades.filter(trade => known(trade.pnl) < 0)
    expect(wins.length).toBeGreaterThan(0)
    expect(losses.length).toBeGreaterThan(0)
    const profit = wins.reduce((sum, trade) => sum + known(trade.pnl), 0)
    const loss = -losses.reduce((sum, trade) => sum + known(trade.pnl), 0)
    expect(portfolio.realizedPnl).toBeCloseTo(profit - loss, 2)
    expect(performance.winRate).toBeCloseTo(wins.length / recentTrades.length * 100, 8)
    expect(performance.profitFactor).toBeCloseTo(profit / loss, 8)
    expect(performance.averageTrade).toBeCloseTo((profit - loss) / recentTrades.length, 8)
  })

  it('reconciles daily history and chart endpoints without double-counting intraday samples', () => {
    const { portfolio, equityCurve, dailyPerformance, performance } = createDemoSnapshot()
    expect(equityCurve[0].equity).toBe(DEMO_STARTING_CAPITAL)
    expect(equityCurve.at(-1)?.equity).toBe(portfolio.equity)
    expect(equityCurve.at(-1)?.returnPct).toBeCloseTo(known(portfolio.totalReturnPct), 8)
    expect(dailyPerformance.reduce((sum, day) => sum + known(day.pnl), 0)).toBeCloseTo(known(portfolio.totalPnl), 2)
    expect(new Set(dailyPerformance.map(day => day.date)).size).toBe(30)
    let previous = DEMO_STARTING_CAPITAL
    for (const day of dailyPerformance) {
      expect(day.returnPct).toBeCloseTo(known(day.pnl) / previous * 100, 8)
      previous += known(day.pnl)
    }
    let peak = DEMO_STARTING_CAPITAL
    for (let index = 0; index < equityCurve.length; index++) {
      const point = equityCurve[index]
      peak = Math.max(peak, known(point.equity))
      expect(point.drawdownPct).toBeCloseTo((known(point.equity) / peak - 1) * 100, 8)
      if (index > 0) expect(Date.parse(point.timestamp)).toBeGreaterThan(Date.parse(equityCurve[index - 1].timestamp))
    }
    expect(performance.maxDrawdown).toBe(Math.min(...equityCurve.map(point => known(point.drawdownPct))))
    expect(known(performance.maxDrawdown)).toBeLessThan(0)
    expect(equityCurve.filter(point => tradingDayInSeoul(point.timestamp) === '2026-09-08').length).toBeGreaterThanOrEqual(1)
  })

  it('reconciles the KST daily baseline, today trades, and realized plus unrealized change', () => {
    const demo = createDemoSnapshot()
    const { today, portfolio, dailyBaseline, dailyPerformance, recentTrades } = demo
    expect(dailyBaseline?.tradingDay).toBe(tradingDayInSeoul(DEMO_TIMESTAMP))
    expect(dailyBaseline?.netCashFlow).toBe(0)
    expect(dailyBaseline?.equity).toBe(demo.equityCurve.find(point => point.timestamp === '2026-09-07T15:00:00.000Z')?.equity)
    const todayTrades = recentTrades.filter(trade => tradingDayInSeoul(trade.closedAt) === dailyBaseline?.tradingDay)
    expect(today.trades).toBe(todayTrades.length)
    expect(today.wins).toBe(todayTrades.filter(trade => known(trade.pnl) > 0).length)
    expect(today.losses).toBe(todayTrades.filter(trade => known(trade.pnl) < 0).length)
    expect(today.realizedPnl).toBeCloseTo(todayTrades.reduce((sum, trade) => sum + known(trade.pnl), 0), 2)
    // Invariant: 91,000 (realized) + 52,700 (unrealized) - 6,200 (fees) = 137,500 (todayPnl)
    expect(portfolio.todayPnl).toBeCloseTo(known(today.realizedPnl) + known(today.unrealizedPnl) - known(today.fees), 2)
    expect(portfolio.todayPnl).toBeCloseTo(known(dailyPerformance.at(-1)!.pnl), 2)
    expect(portfolio.todayReturnPct).toBeCloseTo(known(dailyPerformance.at(-1)!.returnPct), 8)
    expect(today.exposurePct).toBeCloseTo(known(portfolio.exposure) / known(portfolio.equity) * 100, 8)
    expect(known(today.fees)).toBeGreaterThan(0)
  })
})

describe('unknown values and authoritative daily baseline requirements', () => {
  const baseline: DailyBaseline = { equity: 1000, netCashFlow: 100, tradingDay: '2026-09-08', timeZone: 'Asia/Seoul' }
  const unavailable = { todayPnl: null, todayReturnPct: null }

  it('keeps no-data, loading, and error states separate from any snapshot', () => {
    expect(NO_TRADING_DATA).toEqual({ status: 'NO_DATA' })
    const loading: TradingDataState = { status: 'LOADING' }
    const failed: TradingDataState = { status: 'ERROR', error: 'Local data unavailable' }
    expect(loading.snapshot).toBeUndefined()
    expect(failed.snapshot).toBeUndefined()
    // @ts-expect-error An unavailable state cannot carry synthetic financial values.
    const invalid: TradingDataState = { status: 'NO_DATA', snapshot: createDemoSnapshot() }
    expect(invalid.status).toBe('NO_DATA')
  })

  it('adjusts complete data for known external cash flows', () => {
    expect(calculateTodayMetrics(1150, baseline, DEMO_TIMESTAMP)).toEqual({ todayPnl: 50, todayReturnPct: 5 })
    expect(calculateTodayMetrics(850, { ...baseline, netCashFlow: -100 }, DEMO_TIMESTAMP)).toEqual({ todayPnl: -50, todayReturnPct: -5 })
    expect(calculateTodayMetrics(1100, baseline, DEMO_TIMESTAMP)).toEqual({ todayPnl: 0, todayReturnPct: 0 })
  })

  it('does not invent Today Return from missing, stale, or invalid inputs', () => {
    expect(calculateTodayMetrics(1150, null, DEMO_TIMESTAMP)).toEqual(unavailable)
    for (const equity of [null, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(calculateTodayMetrics(equity, baseline, DEMO_TIMESTAMP)).toEqual(unavailable)
    }
    for (const equity of [null, 0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(calculateTodayMetrics(1150, { ...baseline, equity }, DEMO_TIMESTAMP)).toEqual(unavailable)
    }
    for (const netCashFlow of [null, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(calculateTodayMetrics(1150, { ...baseline, netCashFlow }, DEMO_TIMESTAMP)).toEqual(unavailable)
    }
    expect(calculateTodayMetrics(1150, { ...baseline, tradingDay: '2026-09-07' }, DEMO_TIMESTAMP)).toEqual(unavailable)
    expect(calculateTodayMetrics(1150, baseline, 'invalid')).toEqual(unavailable)
  })

  it('uses the KST day rather than the UTC date at midnight', () => {
    expect(tradingDayInSeoul('2026-09-07T14:59:59Z')).toBe('2026-09-07')
    expect(tradingDayInSeoul('2026-09-07T15:00:00Z')).toBe('2026-09-08')
    expect(tradingDayInSeoul('invalid')).toBeNull()
  })
})
