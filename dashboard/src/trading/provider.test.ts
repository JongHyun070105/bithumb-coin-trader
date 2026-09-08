import { describe, expect, it } from 'vitest'
import { createDemoSnapshot } from './demo'
import {
  createTradingState,
  DemoTradingProvider,
  LocalSnapshotProvider,
  NoDataProvider,
  ReadOnlyApiProvider,
} from './provider'
import type { HttpTransport } from './provider'
import type { TradingSnapshot } from './model'

describe('TradingDataProvider', () => {
  it('NoDataProvider returns null snapshot', async () => {
    const provider = new NoDataProvider()
    expect(provider.kind).toBe('NO_DATA')
    const snapshot = await provider.fetchSnapshot()
    expect(snapshot).toBeNull()

    const state = createTradingState(provider.kind, snapshot)
    expect(state.status).toBe('NO_DATA')
  })

  it('DemoTradingProvider returns synthetic demo snapshot', async () => {
    const provider = new DemoTradingProvider()
    expect(provider.kind).toBe('DEMO')
    const snapshot = await provider.fetchSnapshot()
    expect(snapshot).not.toBeNull()
    expect(snapshot?.source.kind).toBe('synthetic')

    const state = createTradingState(provider.kind, snapshot)
    expect(state.status).toBe('SYNTHETIC_DEMO')
    expect(state.snapshot?.portfolio.equity).toBe(10843200)
  })

  it('LocalSnapshotProvider preserves local_snapshot semantics and does NOT promote to REAL_DATA', async () => {
    const provider = new LocalSnapshotProvider()
    expect(provider.kind).toBe('LOCAL_SNAPSHOT')

    const demo = createDemoSnapshot()
    const jsonStr = JSON.stringify(demo)
    const result = provider.loadFromString(jsonStr)

    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.snapshot.portfolio.equity).toBe(10843200)
      expect(result.snapshot.source.kind).toBe('local_snapshot')
    }

    const snapshot = await provider.fetchSnapshot()
    expect(snapshot).not.toBeNull()

    const state = createTradingState(provider.kind, snapshot)
    // Correct invariant: LOCAL_SNAPSHOT stays LOCAL_SNAPSHOT, never REAL_DATA
    expect(state.status).toBe('LOCAL_SNAPSHOT')
    expect(state.snapshot?.source.kind).toBe('local_snapshot')
    expect(state.snapshot?.source.label).toBe('로컬 스냅샷')
  })

  it('LocalSnapshotProvider rejects malformed JSON with descriptive Korean error', () => {
    const provider = new LocalSnapshotProvider()
    const result = provider.loadFromString('INVALID_JSON_HERE{')

    expect(result.success).toBe(false)
    if (!result.success) {
      expect(result.errors[0]).toContain('올바른 JSON 문법이 아닙니다')
    }
  })

  it('ReadOnlyApiProvider throws when transport is missing (fail-closed)', async () => {
    const provider = new ReadOnlyApiProvider()
    expect(provider.kind).toBe('READ_ONLY_API')
    await expect(provider.fetchSnapshot()).rejects.toThrow('읽기 전용 API 어댑터가 설정되지 않았습니다')
  })

  it('ReadOnlyApiProvider validates snapshot when transport is provided', async () => {
    const mockTransport: HttpTransport = {
      get: async <T = unknown>() => createDemoSnapshot() as unknown as T,
    }
    const provider = new ReadOnlyApiProvider(mockTransport)
    const snapshot = await provider.fetchSnapshot()
    expect(snapshot).not.toBeNull()
    expect(snapshot?.portfolio.equity).toBe(10843200)

    const state = createTradingState(provider.kind, snapshot)
    expect(state.status).toBe('READ_ONLY_API')
    // Does not mutate source kind to authoritative
    expect(state.snapshot?.source.kind).toBe('synthetic')
  })

  describe('Source semantics and fail-closed invariants', () => {
    it('rejects promotion to REAL_DATA if source.kind is not authoritative', () => {
      const syntheticSnapshot: TradingSnapshot = createDemoSnapshot()
      expect(syntheticSnapshot.source.kind).toBe('synthetic')

      const state = createTradingState('REAL_DATA', syntheticSnapshot)
      expect(state.status).toBe('ERROR')
      expect(state.error).toContain('권위 있는(authoritative) 출처가 필수입니다')
    })

    it('rejects promotion to REAL_DATA for local_snapshot', () => {
      const localSnapshot: TradingSnapshot = {
        ...createDemoSnapshot(),
        source: { kind: 'local_snapshot', label: 'Local imported file' },
      }

      const state = createTradingState('REAL_DATA', localSnapshot)
      expect(state.status).toBe('ERROR')
      expect(state.error).toContain('권위 있는(authoritative) 출처가 필수입니다')
    })

    it('allows REAL_DATA only when source.kind is genuinely authoritative', () => {
      const authSnapshot: TradingSnapshot = {
        ...createDemoSnapshot(),
        source: { kind: 'authoritative', label: 'Verified Exchange Pipeline' },
      }

      const state = createTradingState('REAL_DATA', authSnapshot)
      expect(state.status).toBe('REAL_DATA')
      expect(state.snapshot?.source.kind).toBe('authoritative')
    })

    it('rejects DEMO state when source.kind is authoritative or local_snapshot', () => {
      const authSnapshot: TradingSnapshot = {
        ...createDemoSnapshot(),
        source: { kind: 'authoritative', label: 'Authoritative Live' },
      }

      const state = createTradingState('DEMO', authSnapshot)
      expect(state.status).toBe('ERROR')
      expect(state.error).toContain('합성(synthetic) 출처 스냅샷만 허용됩니다')
    })
  })
})
