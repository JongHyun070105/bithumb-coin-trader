/**
 * Trading Data Provider Architecture
 *
 * Provides a unified data abstraction for the trading dashboard across:
 * - NO_DATA: initial unpopulated state
 * - DEMO: synthetic coherent demonstration data
 * - LOCAL_SNAPSHOT: user-imported offline JSON snapshot (retains local_snapshot semantics)
 * - READ_ONLY_API: future local development read-only endpoint (preserves payload semantics)
 * - REAL_DATA: strictly requires snapshot.source.kind === 'authoritative'
 */

import { createDemoSnapshot } from './demo'
import type { TradingDataState, TradingSnapshot } from './model'
import { normalizeTradingSnapshot, validateTradingSnapshot } from './snapshotValidation'

export type DataSourceKind = 'NO_DATA' | 'DEMO' | 'LOCAL_SNAPSHOT' | 'READ_ONLY_API' | 'REAL_DATA'

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
 * Never promotes local data to authoritative provenance.
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
    // Force local_snapshot source semantics to prevent spoofing authoritative provenance via offline file
    const localSnapshot: TradingSnapshot = {
      ...normalized,
      source: {
        kind: 'local_snapshot',
        label: '로컬 스냅샷',
      },
    }

    this.currentSnapshot = localSnapshot
    return { success: true, snapshot: localSnapshot }
  }

  async loadFromFile(file: File): Promise<{ success: true; snapshot: TradingSnapshot } | { success: false; errors: string[] }> {
    const MAX_FILE_SIZE = 5 * 1024 * 1024 // 5 MiB
    if (file.size > MAX_FILE_SIZE) {
      return { success: false, errors: [`파일 크기가 제한(5MB)을 초과했습니다. (현재 크기: ${(file.size / (1024 * 1024)).toFixed(2)}MB)`] }
    }
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

import { createLocalhostApiClient, ReadOnlyTradingApiClient, type ApiClientOptions } from './apiClient'

export class ReadOnlyApiProvider implements TradingDataProvider {
  readonly kind: DataSourceKind = 'READ_ONLY_API'
  readonly label = '읽기 전용 API'
  private transport?: HttpTransport
  private client?: ReadOnlyTradingApiClient

  constructor(target?: HttpTransport | ReadOnlyTradingApiClient | string, options?: ApiClientOptions) {
    if (typeof target === 'string') {
      this.client = createLocalhostApiClient({ baseUrl: target, ...options })
    } else if (target instanceof ReadOnlyTradingApiClient) {
      this.client = target
    } else if (target && typeof target.get === 'function') {
      this.transport = target
    }
  }

  async fetchSnapshot(): Promise<TradingSnapshot | null> {
    if (this.client) {
      return this.client.getTradingSnapshot()
    }
    if (this.transport) {
      const raw = await this.transport.get<unknown>('/api/trading/snapshot')
      const validation = validateTradingSnapshot(raw)
      if (!validation.valid || !validation.data) {
        throw new Error(`API 응답 검증 실패: ${validation.errors.join(', ')}`)
      }
      return normalizeTradingSnapshot(validation.data)
    }
    throw new Error('읽기 전용 API 어댑터가 설정되지 않았습니다 (기본 비활성화).')
  }
}

/**
 * Helper to produce TradingDataState from snapshot and source kind.
 *
 * Epistemic Rules:
 * - LOCAL_SNAPSHOT -> status: LOCAL_SNAPSHOT, source.kind: local_snapshot
 * - READ_ONLY_API -> status: READ_ONLY_API, preserves payload source semantics
 * - REAL_DATA -> status: REAL_DATA, requires snapshot.source.kind === 'authoritative' (Fail-Closed)
 * - Schema validity != authoritative provenance.
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
    if (snapshot.source.kind !== 'synthetic') {
      return {
        status: 'ERROR',
        error: '데모 상태는 합성(synthetic) 출처 스냅샷만 허용됩니다.',
      }
    }
    return {
      status: 'SYNTHETIC_DEMO',
      snapshot,
    }
  }

  if (kind === 'LOCAL_SNAPSHOT') {
    return {
      status: 'LOCAL_SNAPSHOT',
      snapshot: {
        ...snapshot,
        source: {
          kind: 'local_snapshot',
          label: '로컬 스냅샷',
        },
      },
      isStale: options?.isStale,
    }
  }

  if (kind === 'READ_ONLY_API') {
    return {
      status: 'READ_ONLY_API',
      snapshot: { ...snapshot },
      isStale: options?.isStale,
    }
  }

  if (kind === 'REAL_DATA') {
    // Fail-Closed: only genuinely authoritative payloads can be promoted to REAL_DATA
    if (snapshot.source.kind !== 'authoritative') {
      return {
        status: 'ERROR',
        error: `실제 데이터(REAL_DATA) 상태는 권위 있는(authoritative) 출처가 필수입니다. (현재 출처: ${snapshot.source.kind})`,
      }
    }
    return {
      status: 'REAL_DATA',
      snapshot,
      isStale: options?.isStale,
    }
  }

  return { status: 'NO_DATA' }
}
