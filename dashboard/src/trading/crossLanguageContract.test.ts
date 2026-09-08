import fs from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { createTradingState } from './provider'
import { normalizeTradingSnapshot, validateTradingSnapshot } from './snapshotValidation'

describe('Cross-Language Contract: Python dashboard_snapshot -> TypeScript Validator', () => {
  it('validates golden snapshot produced by Python dashboard_snapshot builder without any errors', () => {
    const goldenPath = path.resolve(__dirname, '../../tests/golden/python_trading_snapshot_v1.json')
    expect(fs.existsSync(goldenPath)).toBe(true)

    const rawText = fs.readFileSync(goldenPath, 'utf-8')
    const parsed = JSON.parse(rawText)

    const result = validateTradingSnapshot(parsed)
    expect(result.valid).toBe(true)
    expect(result.errors).toEqual([])
    expect(result.data).toBeDefined()

    const snapshot = result.data!
    expect(snapshot.schemaVersion).toBe(1)
    expect(snapshot.source.kind).toBe('local_snapshot')
    expect(snapshot.source.label).toBe('오프라인 FillLedger 스냅샷')
    expect(snapshot.portfolio.equity).toBe(14620000)
    expect(snapshot.portfolio.cash).toBe(8500000)
    expect(snapshot.portfolio.exposure).toBe(6120000)
    expect(snapshot.portfolio.realizedPnl).toBe(98350)
    expect(snapshot.portfolio.unrealizedPnl).toBe(217050)
    expect(snapshot.positions).toHaveLength(2)
    expect(snapshot.recentTrades).toEqual([])

    // Accounting invariant: sum of open position pnl == portfolio.unrealizedPnl
    const sumPosPnl = snapshot.positions.reduce((acc, p) => acc + (p.pnl ?? 0), 0)
    expect(sumPosPnl).toBe(snapshot.portfolio.unrealizedPnl)

    // Fail-honest P2: entryFee is null because FillLedger does not separate acquisition fees after sells
    expect(snapshot.positions.every((p) => p.entryFee === null)).toBe(true)

    // P5: No closed round-trip trades -> winRate, profitFactor, averageTrade are null
    expect(snapshot.performance.winRate).toBeNull()
    expect(snapshot.performance.profitFactor).toBeNull()
    expect(snapshot.performance.averageTrade).toBeNull()
    expect(snapshot.performance.totalReturn).toBe(snapshot.portfolio.totalReturnPct)

    // State creation preserves local_snapshot semantics
    const localState = createTradingState('LOCAL_SNAPSHOT', snapshot)
    expect(localState.status).toBe('LOCAL_SNAPSHOT')
    expect(localState.snapshot?.source.kind).toBe('local_snapshot')

    const apiState = createTradingState('READ_ONLY_API', snapshot)
    expect(apiState.status).toBe('READ_ONLY_API')
    expect(apiState.snapshot?.source.kind).toBe('local_snapshot')

    // Normalization succeeds
    const normalized = normalizeTradingSnapshot(snapshot)
    expect(normalized.schemaVersion).toBe(1)
    expect(normalized.portfolio.equity).toBe(14620000)
  })
})
