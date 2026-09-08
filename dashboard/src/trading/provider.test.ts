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

  it('LocalSnapshotProvider parses valid JSON and populates snapshot', async () => {
    const provider = new LocalSnapshotProvider()
    expect(provider.kind).toBe('LOCAL_SNAPSHOT')

    const demo = createDemoSnapshot()
    const jsonStr = JSON.stringify(demo)
    const result = provider.loadFromString(jsonStr)

    expect(result.success).toBe(true)
    if (result.success) {
      expect(result.snapshot.portfolio.equity).toBe(10843200)
    }

    const snapshot = await provider.fetchSnapshot()
    expect(snapshot).not.toBeNull()

    const state = createTradingState(provider.kind, snapshot)
    expect(state.status).toBe('REAL_DATA')
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
  })
})
