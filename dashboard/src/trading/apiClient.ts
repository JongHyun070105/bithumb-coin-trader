/**
 * Localhost-First Read-Only Trading API Client
 *
 * Designed strictly for future local development inspection.
 * - Targets localhost / 127.0.0.1 only (rejects external / AWS URLs)
 * - Read-only GET endpoints only
 * - Timeout handling with AbortController
 * - Zero credentials transmitted
 * - Injects transport/fetcher to maintain complete air-gap isolation in default builds
 */

import type { ApiHealthResponse, ReadOnlyTradingApiEndpoints } from './apiContract'
import type { BotStatus, PortfolioSummary, Position, Trade, TradingSnapshot } from './model'
import { normalizeTradingSnapshot, validateTradingSnapshot } from './snapshotValidation'

export interface ApiClientOptions {
  baseUrl?: string
  timeoutMs?: number
  fetcher?: (url: string, init?: RequestInit) => Promise<Response>
}

export class ReadOnlyTradingApiClient implements ReadOnlyTradingApiEndpoints {
  private readonly baseUrl: string
  private readonly timeoutMs: number
  private readonly fetcher?: (url: string, init?: RequestInit) => Promise<Response>

  constructor(options: ApiClientOptions = {}) {
    const rawUrl = options.baseUrl ?? 'http://127.0.0.1:8000'
    this.assertLocalhostOnly(rawUrl)
    this.baseUrl = rawUrl.replace(/\/+$/, '')
    this.timeoutMs = options.timeoutMs ?? 5000
    this.fetcher = options.fetcher
  }

  private assertLocalhostOnly(url: string): void {
    const parsed = new URL(url)
    const host = parsed.hostname.toLowerCase()
    const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1'
    if (!isLocal) {
      throw new Error(`보안 위반: 읽기 전용 API는 localhost / 127.0.0.1에서만 연결할 수 있습니다. (요청 호스트: ${host})`)
    }
  }

  private async request<T>(path: string): Promise<T> {
    if (!this.fetcher) {
      throw new Error('API 연결기가 설정되지 않았습니다 (기본 오프라인 격리 모드).')
    }

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)

    try {
      const targetUrl = `${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`
      const response = await this.fetcher(targetUrl, {
        method: 'GET',
        signal: controller.signal,
        headers: { Accept: 'application/json' },
        credentials: 'omit',
      })

      if (!response.ok) {
        throw new Error(`API 응답 오류 (상태 코드: ${response.status})`)
      }

      const data = (await response.json()) as T
      return data
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        throw new Error(`API 요청 시간 초과 (${this.timeoutMs}ms)`)
      }
      if (err instanceof Error) {
        throw new Error(`API 통신 실패: ${err.message}`)
      }
      throw new Error('알 수 없는 API 통신 오류가 발생했습니다.')
    } finally {
      clearTimeout(timer)
    }
  }

  async getHealth(): Promise<ApiHealthResponse> {
    return this.request<ApiHealthResponse>('/api/health')
  }

  async getTradingSnapshot(): Promise<TradingSnapshot> {
    const raw = await this.request<unknown>('/api/trading/snapshot')
    const validation = validateTradingSnapshot(raw)
    if (!validation.valid || !validation.data) {
      throw new Error(`스냅샷 데이터 무결성 검증 실패: ${validation.errors.join(', ')}`)
    }
    return normalizeTradingSnapshot(validation.data)
  }

  async getPortfolio(): Promise<PortfolioSummary> {
    return this.request<PortfolioSummary>('/api/portfolio')
  }

  async getPositions(): Promise<Position[]> {
    return this.request<Position[]>('/api/positions')
  }

  async getTrades(): Promise<Trade[]> {
    return this.request<Trade[]>('/api/trades')
  }

  async getPerformance(): Promise<TradingSnapshot['performance']> {
    return this.request<TradingSnapshot['performance']>('/api/performance')
  }

  async getBotStatus(): Promise<BotStatus> {
    return this.request<BotStatus>('/api/bot/status')
  }
}
