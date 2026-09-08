import { describe, expect, it, vi } from 'vitest'
import { ReadOnlyTradingApiClient } from './apiClient'
import { createDemoSnapshot } from './demo'

describe('ReadOnlyTradingApiClient', () => {
  it('allows localhost and 127.0.0.1 base URLs', () => {
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'http://127.0.0.1:8000' })).not.toThrow()
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'http://localhost:3000' })).not.toThrow()
  })

  it('strictly rejects external, AWS, and exchange URLs', () => {
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'https://api.bithumb.com' })).toThrow('보안 위반')
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'https://s3.amazonaws.com' })).toThrow('보안 위반')
    expect(() => new ReadOnlyTradingApiClient({ baseUrl: 'https://evil.attacker.com' })).toThrow('보안 위반')
  })

  it('fails closed when fetcher is not provided', async () => {
    const client = new ReadOnlyTradingApiClient()
    await expect(client.getHealth()).rejects.toThrow('API 연결기가 설정되지 않았습니다')
  })

  it('fetches and validates trading snapshot with mock fetcher', async () => {
    const mockSnapshot = createDemoSnapshot()
    const mockFetcher = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => mockSnapshot,
    })

    const client = new ReadOnlyTradingApiClient({
      baseUrl: 'http://127.0.0.1:8000',
      fetcher: mockFetcher,
    })

    const snapshot = await client.getTradingSnapshot()
    expect(snapshot.portfolio.equity).toBe(10843200)
    expect(mockFetcher).toHaveBeenCalledWith(
      'http://127.0.0.1:8000/api/trading/snapshot',
      expect.objectContaining({
        method: 'GET',
        credentials: 'omit',
      }),
    )
  })

  it('handles HTTP error status codes gracefully', async () => {
    const mockFetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    })

    const client = new ReadOnlyTradingApiClient({
      baseUrl: 'http://127.0.0.1:8000',
      fetcher: mockFetcher,
    })

    await expect(client.getHealth()).rejects.toThrow('API 응답 오류 (상태 코드: 503)')
  })

  it('handles timeout gracefully', async () => {
    const mockFetcher = vi.fn().mockImplementation((_url, init) => {
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          const err = new Error('The operation was aborted')
          err.name = 'AbortError'
          reject(err)
        })
      })
    })

    const client = new ReadOnlyTradingApiClient({
      baseUrl: 'http://127.0.0.1:8000',
      timeoutMs: 50,
      fetcher: mockFetcher,
    })

    await expect(client.getHealth()).rejects.toThrow('API 요청 시간 초과 (50ms)')
  })
})
