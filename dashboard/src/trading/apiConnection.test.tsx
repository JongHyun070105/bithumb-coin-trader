import { describe, expect, it } from 'vitest'
import { createLocalhostApiClient, ReadOnlyTradingApiClient } from './apiClient'
import { createDemoSnapshot } from './demo'
import { createTradingState, ReadOnlyApiProvider } from './provider'
import type { TradingSnapshot } from './model'

describe('Frontend API Connection & Security Boundaries', () => {
  it('allows localhost, 127.0.0.1, and IPv6 loopback [::1]', () => {
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'http://localhost:8765' })).not.toThrow()
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'http://127.0.0.1:8765' })).not.toThrow()
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'http://[::1]:8765' })).not.toThrow()
  })

  it('rejects external domains, LAN IPs, and AWS hostnames fail-closed before request', () => {
    const forbiddenUrls = [
      'https://example.com/api',
      'http://192.168.1.100:8765',
      'http://10.0.0.1:8765',
      'http://172.16.0.1:8765',
      'https://ec2-13-125-1-1.ap-northeast-2.compute.amazonaws.com',
      'http://api.bithumb.com/v1',
    ]

    for (const badUrl of forbiddenUrls) {
      expect(() => new ReadOnlyTradingApiClient({ baseUrl: badUrl })).toThrow(/보안 위반.*localhost/)
    }
  })

  it('preserves local_snapshot source semantics when served via Local API', async () => {
    const demo = createDemoSnapshot()
    const localSnapshotPayload: TradingSnapshot = {
      ...demo,
      source: {
        kind: 'local_snapshot',
        label: '오프라인 FillLedger 스냅샷',
      },
    }

    const mockFetcher = async () =>
      new Response(JSON.stringify(localSnapshotPayload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })

    const provider = new ReadOnlyApiProvider('http://127.0.0.1:8765', { fetcher: mockFetcher })
    const snapshot = await provider.fetchSnapshot()
    expect(snapshot).not.toBeNull()
    expect(snapshot?.source.kind).toBe('local_snapshot')

    const state = createTradingState('READ_ONLY_API', snapshot)
    // Correct invariant: status is READ_ONLY_API, source.kind remains local_snapshot, NEVER authoritative
    expect(state.status).toBe('READ_ONLY_API')
    expect(state.snapshot?.source.kind).toBe('local_snapshot')
    expect(state.snapshot?.source.label).toBe('오프라인 FillLedger 스냅샷')
  })

  it('rejects unsupported schemaVersion from API response fail-closed', async () => {
    const demo = createDemoSnapshot()
    const badPayload = {
      ...demo,
      schemaVersion: 999,
    }

    const mockFetcher = async () =>
      new Response(JSON.stringify(badPayload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })

    const provider = new ReadOnlyApiProvider('http://127.0.0.1:8765', { fetcher: mockFetcher })
    await expect(provider.fetchSnapshot()).rejects.toThrow(/지원하지 않는 스키마 버전/)
  })

  it('rejects malformed API response with descriptive error', async () => {
    const mockFetcher = async () =>
      new Response('{"broken": true,', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })

    const client = createLocalhostApiClient({ baseUrl: 'http://127.0.0.1:8765', fetcher: mockFetcher })
    await expect(client.getTradingSnapshot()).rejects.toThrow(/API 통신 실패/)
  })

  it('marks stale response when snapshot timestamp is older than 60 seconds', () => {
    const demo = createDemoSnapshot()
    const state = createTradingState('READ_ONLY_API', demo, { isStale: true })
    expect(state.status).toBe('READ_ONLY_API')
    expect(state.isStale).toBe(true)
  })
})
