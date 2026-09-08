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
    expect(result.errors.some(e => e.includes('타임스탬프'))).toBe(true)
    expect(result.errors.some(e => e.includes('모드'))).toBe(true)
  })

  it('does NOT replace missing portfolio fields with zero', () => {
    const bad = createDemoSnapshot()
    // @ts-expect-error test invalid field
    delete bad.portfolio.equity
    const result = validateTradingSnapshot(bad)
    expect(result.valid).toBe(false)
    expect(result.errors.some(e => e.includes('equity'))).toBe(true)
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
})
