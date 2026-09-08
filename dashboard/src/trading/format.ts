import { calculateTodayMetrics } from './model'
import type { TradingDataState } from './model'

export * from './formatters'

/**
 * Never relabel a synthetic payload as authoritative, or infer returns without a daily baseline.
 * Enforces Fail-Closed: REAL_DATA strictly requires snapshot.source.kind === 'authoritative'.
 */
export function prepareTradingState(data: TradingDataState): TradingDataState {
  if (
    data.status !== 'REAL_DATA' &&
    data.status !== 'SYNTHETIC_DEMO' &&
    data.status !== 'LOCAL_SNAPSHOT' &&
    data.status !== 'READ_ONLY_API'
  ) {
    return data
  }
  if (!data.snapshot) return data

  if (data.status === 'SYNTHETIC_DEMO' && data.snapshot.source.kind !== 'synthetic') {
    return { status: 'ERROR', error: '데이터 소스 종류가 선택된 모드와 일치하지 않습니다.' }
  }

  if (data.status === 'REAL_DATA' && data.snapshot.source.kind !== 'authoritative') {
    return { status: 'ERROR', error: '실제 데이터는 권위 있는(authoritative) 출처가 필수입니다.' }
  }

  if (data.status === 'SYNTHETIC_DEMO') return data

  const daily = calculateTodayMetrics(
    data.snapshot.portfolio.equity,
    data.snapshot.dailyBaseline,
    data.snapshot.timestamp,
  )
  return {
    ...data,
    snapshot: {
      ...data.snapshot,
      portfolio: { ...data.snapshot.portfolio, ...daily },
    },
  }
}
