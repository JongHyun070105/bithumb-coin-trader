/**
 * Trading Data Provider Architecture
 *
 * Provides a unified data abstraction for the trading dashboard across:
 * - NO_DATA: initial unpopulated state
 * - DEMO: synthetic coherent demonstration data
 * - LOCAL_SNAPSHOT: user-imported offline JSON snapshot
 * - READ_ONLY_API: future local development read-only endpoint (default: disabled)
 */

import { createDemoSnapshot } from './demo'
import type { TradingDataState, TradingSnapshot } from './model'
import { normalizeTradingSnapshot, validateTradingSnapshot } from './snapshotValidation'

export type DataSourceKind = 'NO_DATA' | 'DEMO' | 'LOCAL_SNAPSHOT' | 'READ_ONLY_API'

export interface TradingDataProvider {
  readonly kind: DataSourceKind
  readonly label: string
  fetchSnapshot(): Promise<TradingSnapshot | null>
}

/**
 * 1. NoDataProvider: returns null (representing unstarted or empty state)
 */
export class NoDataProvider implements TradingDataProvider {
  readonly kind: DataSourceKind = 'NO_DATA'
  readonly label = '트레이딩 데이터 없음'

  async fetchSnapshot(): Promise<TradingSnapshot | null> {
    return null
  }
}

/**
 * 2. DemoTradingProvider: returns synthetic deterministic demonstration snapshot
 */
export class DemoTradingProvider implements TradingDataProvider {
  readonly kind: DataSourceKind = 'DEMO'
  readonly label = '데모 데이터'

  async fetchSnapshot(): Promise<TradingSnapshot | null> {
    return createDemoSnapshot()
  }
}

/**
 * 3. LocalSnapshotProvider: imports and validates an offline JSON file/string
 */
export class LocalSnapshotProvider implements TradingDataProvider {
  readonly kind: DataSourceKind = 'LOCAL_SNAPSHOT'
  readonly label = '로컬 스냅샷'
  private currentSnapshot: TradingSnapshot | null = null

  constructor(initialSnapshot?: TradingSnapshot | null) {
    this.currentSnapshot = initialSnapshot ?? null
  }

  loadFromString(jsonString: string): { success: true; snapshot: TradingSnapshot } | { success: false; errors: string[] } {
    let parsed: unknown
    try {
      parsed = JSON.parse(jsonString)
    } catch {
      return { success: false, errors: ['올바른 JSON 문법이 아닙니다. 파일 내용을 확인하세요.'] }
    }

    const validation = validateTradingSnapshot(parsed)
    if (!validation.valid || !validation.data) {
      return { success: false, errors: validation.errors }
    }

    const normalized = normalizeTradingSnapshot(validation.data)
    this.currentSnapshot = normalized
    return { success: true, snapshot: normalized }
  }

  async loadFromFile(file: File): Promise<{ success: true; snapshot: TradingSnapshot } | { success: false; errors: string[] }> {
    return new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const text = e.target?.result
        if (typeof text !== 'string') {
          resolve({ success: false, errors: ['파일을 텍스트로 읽을 수 없습니다.'] })
          return
        }
        resolve(this.loadFromString(text))
      }
      reader.onerror = () => {
        resolve({ success: false, errors: ['파일 읽기 작업 중 오류가 발생했습니다.'] })
      }
      reader.readAsText(file)
    })
  }

  async fetchSnapshot(): Promise<TradingSnapshot | null> {
    return this.currentSnapshot
  }
}

/**
 * 4. ReadOnlyApiProvider: future local adapter (disabled by default)
 */
export interface HttpTransport {
  get<T = unknown>(path: string): Promise<T>
}

export class ReadOnlyApiProvider implements TradingDataProvider {
  readonly kind: DataSourceKind = 'READ_ONLY_API'
  readonly label = '읽기 전용 API'
  private transport?: HttpTransport

  constructor(transport?: HttpTransport) {
    this.transport = transport
  }

  async fetchSnapshot(): Promise<TradingSnapshot | null> {
    if (!this.transport) {
      throw new Error('읽기 전용 API 어댑터가 설정되지 않았습니다 (기본 비활성화).')
    }
    const raw = await this.transport.get<unknown>('/api/trading/snapshot')
    const validation = validateTradingSnapshot(raw)
    if (!validation.valid || !validation.data) {
      throw new Error(`API 응답 검증 실패: ${validation.errors.join(', ')}`)
    }
    return normalizeTradingSnapshot(validation.data)
  }
}

/**
 * Helper to produce TradingDataState from snapshot and source kind
 */
export function createTradingState(
  kind: DataSourceKind,
  snapshot: TradingSnapshot | null,
  options?: { isStale?: boolean; error?: string },
): TradingDataState {
  if (options?.error) {
    return {
      status: 'ERROR',
      error: options.error,
    }
  }

  if (kind === 'NO_DATA' || !snapshot) {
    return {
      status: 'NO_DATA',
    }
  }

  if (kind === 'DEMO') {
    return {
      status: 'SYNTHETIC_DEMO',
      snapshot,
    }
  }

  // Real or imported offline data
  return {
    status: 'REAL_DATA',
    snapshot: {
      ...snapshot,
      source: {
        kind: 'authoritative',
        label: kind === 'LOCAL_SNAPSHOT' ? '로컬 스냅샷' : '읽기 전용 API',
      },
    },
    isStale: options?.isStale,
  }
}
