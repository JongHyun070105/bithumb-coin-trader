import { describe, expect, it } from 'vitest'
import { createDemoSnapshot } from './demo'
import { exportDemoSnapshotJson, normalizeTradingSnapshot, validateTradingSnapshot } from './snapshotValidation'

describe('snapshotValidation', () => {
  it('validates a valid snapshot successfully', () => {
    const valid = createDemoSnapshot()
    const result = validateTradingSnapshot(valid)
    expect(result.valid).toBe(true)
    expect(result.errors).toHaveLength(0)
    expect(result.data).toBeDefined()
  })

  it('rejects non-object and null roots with descriptive Korean error', () => {
    expect(validateTradingSnapshot(null).valid).toBe(false)
    expect(validateTradingSnapshot(null).errors[0]).toContain('올바른 JSON 객체가 아닙니다')

    expect(validateTradingSnapshot('string').valid).toBe(false)
    expect(validateTradingSnapshot([]).valid).toBe(false)
  })

  it('rejects invalid or missing timestamps and modes', () => {
    const bad = { ...createDemoSnapshot(), timestamp: 'invalid-time', mode: 'UNKNOWN' }
    const result = validateTradingSnapshot(bad)
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.includes('timestamp'))).toBe(true)
    expect(result.errors.some(e => e.includes('mode'))).toBe(true)
  })

  it('does NOT replace missing portfolio fields with zero', () => {
    const bad = createDemoSnapshot()
    // @ts-expect-error test invalid field
    delete bad.portfolio.equity
    const result = validateTradingSnapshot(bad)
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.includes('portfolio.equity'))).toBe(true)
    // Ensures raw missing property is not silently turned into 0
    expect((bad.portfolio as unknown as Record<string, unknown>).equity).toBeUndefined()
  })

  it('allows null fields in portfolio as explicit unknown values', () => {
    const withNulls = createDemoSnapshot()
    withNulls.portfolio.todayPnl = null
    withNulls.portfolio.todayReturnPct = null
    const result = validateTradingSnapshot(withNulls)
    expect(result.valid).toBe(true)
    expect(result.data?.portfolio.todayPnl).toBeNull()
  })

  it('normalizes trading snapshot preserving explicit nulls', () => {
    const snapshot = createDemoSnapshot()
    snapshot.portfolio.todayPnl = null
    const normalized = normalizeTradingSnapshot(snapshot)
    expect(normalized.portfolio.todayPnl).toBeNull()
    expect(normalized.portfolio.equity).toBe(snapshot.portfolio.equity)
  })

  it('exports demo snapshot annotated with synthetic: true', () => {
    const snapshot = createDemoSnapshot()
    const jsonString = exportDemoSnapshotJson(snapshot)
    const parsed = JSON.parse(jsonString)

    expect(parsed.synthetic).toBe(true)
    expect(parsed.notice).toContain('SYNTHETIC DEVELOPMENT DATA ONLY')
    expect(parsed.portfolio.equity).toBe(snapshot.portfolio.equity)
  })

  describe('Strict structural negative tests', () => {
    it('rejects dailyPerformance item with string pnl', () => {
      const bad = createDemoSnapshot()
      // @ts-expect-error invalid type test
      bad.dailyPerformance = [{ date: '2026-09-07', pnl: '1000', returnPct: 1.0 }]
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('dailyPerformance[0].pnl'))).toBe(true)
    })

    it('rejects dailyPerformance with invalid date format', () => {
      const bad = createDemoSnapshot()
      bad.dailyPerformance = [{ date: '2026/09/07', pnl: 1000, returnPct: 1.0 }]
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('dailyPerformance[0].date'))).toBe(true)
    })

    it('rejects invalid equityCurve timestamp', () => {
      const bad = createDemoSnapshot()
      bad.equityCurve = [{ timestamp: 'not-a-timestamp', equity: 10000000, returnPct: 0, drawdownPct: 0 }]
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('equityCurve[0].timestamp'))).toBe(true)
    })

    it('rejects position with missing or invalid openedAt', () => {
      const bad = createDemoSnapshot()
      bad.positions[0] = { ...bad.positions[0], openedAt: 'invalid-openedAt' }
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('positions[0].openedAt'))).toBe(true)
    })

    it('rejects trade with invalid openedAt or closedAt', () => {
      const bad = createDemoSnapshot()
      bad.recentTrades[0].openedAt = 'invalid'
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('recentTrades[0].openedAt'))).toBe(true)
    })

    it('rejects trade where openedAt is later than closedAt (semantic sanity)', () => {
      const bad = createDemoSnapshot()
      bad.recentTrades[0].openedAt = '2026-09-08T10:00:00Z'
      bad.recentTrades[0].closedAt = '2026-09-08T09:00:00Z'
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('진입 시각(openedAt)이 청산 시각(closedAt)보다 미래일 수 없습니다'))).toBe(true)
    })

    it('rejects negative botStatus error count', () => {
      const bad = createDemoSnapshot()
      bad.botStatus.errors = -1
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('botStatus.errors'))).toBe(true)
    })

    it('rejects negative botStatus uptimeSeconds or todayTrades', () => {
      const bad = createDemoSnapshot()
      bad.botStatus.uptimeSeconds = -50
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('botStatus.uptimeSeconds'))).toBe(true)
    })

    it('rejects negative today trade counts (trades, wins, losses)', () => {
      const bad = createDemoSnapshot()
      bad.today.wins = -2
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('today.wins'))).toBe(true)
    })

    it('rejects malformed dailyBaseline (wrong timezone or invalid tradingDay)', () => {
      const bad = createDemoSnapshot()
      bad.dailyBaseline = {
        equity: 10000000,
        netCashFlow: 0,
        tradingDay: '2026-09-08',
        timeZone: 'UTC' as unknown as 'Asia/Seoul',
      }
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('dailyBaseline.timeZone'))).toBe(true)
    })

    it('rejects missing schemaVersion', () => {
      const bad = createDemoSnapshot()
      // @ts-expect-error testing missing schemaVersion
      delete bad.schemaVersion
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('schemaVersion: 필드가 누락되었습니다'))).toBe(true)
    })

    it('rejects unsupported schemaVersion (e.g. schemaVersion 999) fail-closed', () => {
      const bad = { ...createDemoSnapshot(), schemaVersion: 999 }
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('지원하지 않는 스키마 버전'))).toBe(true)
      expect(result.errors.some(e => e.includes('999'))).toBe(true)
    })

    it('rejects non-numeric schemaVersion', () => {
      const bad = { ...createDemoSnapshot(), schemaVersion: '1' }
      const result = validateTradingSnapshot(bad)
      expect(result.valid).toBe(false)
      expect(result.errors.some(e => e.includes('지원하지 않는 스키마 버전'))).toBe(true)
    })
  })
})
