/**
 * Localhost-First Read-Only Trading API Client
 *
 * Designed strictly for future local development inspection.
 * - Targets localhost / 127.0.0.1 only (rejects external / AWS URLs)
 * - Read-only GET endpoints only
 * - Timeout handling with AbortController
 * - Zero credentials transmitted
 * - Injects transport/fetcher to maintain complete air-gap isolation in default builds
 * - Every single endpoint enforces runtime validation before returning data
 */

import type { ApiHealthResponse, ReadOnlyTradingApiEndpoints } from './apiContract'
import type { BotStatus, PortfolioSummary, Position, Trade, TradingSnapshot } from './model'
import {
  normalizeTradingSnapshot,
  validateBotStatus,
  validatePerformance,
  validatePortfolioSummary,
  validatePositions,
  validateTrades,
  validateTradingSnapshot,
} from './snapshotValidation'

export interface ApiClientOptions {
  baseUrl?: string
  timeoutMs?: number
  fetcher?: (url: string, init?: RequestInit) => Promise<Response>
}

/**
 * Factory for creating a localhost-guarded API client using browser native fetch.
 * Explicitly guards against non-localhost targets before attaching fetch.
 */
export function createLocalhostApiClient(options: ApiClientOptions = {}): ReadOnlyTradingApiClient {
  const fetcher =
    options.fetcher ??
    (typeof globalThis.fetch === 'function'
      ? (url: string, init?: RequestInit) => globalThis.fetch(url, init)
      : undefined)
  return new ReadOnlyTradingApiClient({ ...options, fetcher })
}

export class ReadOnlyTradingApiClient implements ReadOnlyTradingApiEndpoints {
  private readonly baseUrl: string
  private readonly timeoutMs: number
  private readonly fetcher?: (url: string, init?: RequestInit) => Promise<Response>

  constructor(options: ApiClientOptions = {}) {
    const rawUrl = options.baseUrl ?? 'http://127.0.0.1:8765'
    this.assertLocalhostOnly(rawUrl)
    this.baseUrl = rawUrl.replace(/\/+$/, '')
    this.timeoutMs = options.timeoutMs ?? 5000
    this.fetcher = options.fetcher
  }

  private assertLocalhostOnly(url: string): void {
    const parsed = new URL(url)
    const host = parsed.hostname.toLowerCase()
    const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]'
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
    const data = await this.request<unknown>('/api/health')
    if (typeof data !== 'object' || data === null) {
      throw new Error('Health check 응답이 객체가 아닙니다.')
    }
    const d = data as Record<string, unknown>
    if (d.status !== 'ok' && d.status !== 'degraded') throw new Error('Health check 상태가 올바르지 않습니다.')
    if (typeof d.version !== 'string') throw new Error('Health check 버전 정보가 누락되었습니다.')
    if (d.readOnly !== true) throw new Error('Health check readOnly 속성이 true가 아닙니다.')
    return data as ApiHealthResponse
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
    const raw = await this.request<unknown>('/api/portfolio')
    const errors = validatePortfolioSummary(raw, 'portfolio')
    if (errors.length > 0) {
      throw new Error(`포트폴리오 응답 런타임 검증 실패: ${errors.join(', ')}`)
    }
    return raw as PortfolioSummary
  }

  async getPositions(): Promise<Position[]> {
    const raw = await this.request<unknown>('/api/positions')
    const errors = validatePositions(raw, 'positions')
    if (errors.length > 0) {
      throw new Error(`보유 포지션 응답 런타임 검증 실패: ${errors.join(', ')}`)
    }
    return raw as Position[]
  }

  async getTrades(): Promise<Trade[]> {
    const raw = await this.request<unknown>('/api/trades')
    const errors = validateTrades(raw, 'recentTrades')
    if (errors.length > 0) {
      throw new Error(`거래 내역 응답 런타임 검증 실패: ${errors.join(', ')}`)
    }
    return raw as Trade[]
  }

  async getPerformance(): Promise<TradingSnapshot['performance']> {
    const raw = await this.request<unknown>('/api/performance')
    const errors = validatePerformance(raw, 'performance')
    if (errors.length > 0) {
      throw new Error(`성과 분석 응답 런타임 검증 실패: ${errors.join(', ')}`)
    }
    return raw as TradingSnapshot['performance']
  }

  async getBotStatus(): Promise<BotStatus> {
    const raw = await this.request<unknown>('/api/bot/status')
    const errors = validateBotStatus(raw, 'botStatus')
    if (errors.length > 0) {
      throw new Error(`봇 상태 응답 런타임 검증 실패: ${errors.join(', ')}`)
    }
    return raw as BotStatus
  }
}
