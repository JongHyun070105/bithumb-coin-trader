import type { TradingSnapshot } from './model'

export interface ValidationResult {
  valid: boolean
  data?: TradingSnapshot
  errors: string[]
}

function isObject(val: unknown): val is Record<string, unknown> {
  return typeof val === 'object' && val !== null && !Array.isArray(val)
}

function isNumberOrNull(val: unknown): val is number | null {
  return val === null || (typeof val === 'number' && Number.isFinite(val))
}

/**
 * Validates raw JSON payload against TradingSnapshot schema.
 * Never replaces missing/invalid fields with zero. Returns descriptive Korean errors.
 */
export function validateTradingSnapshot(raw: unknown): ValidationResult {
  const errors: string[] = []

  if (!isObject(raw)) {
    return { valid: false, errors: ['스냅샷 최상위 데이터가 올바른 JSON 객체가 아닙니다.'] }
  }

  // 1. timestamp
  if (typeof raw.timestamp !== 'string' || !Number.isFinite(Date.parse(raw.timestamp))) {
    errors.push('유효하지 않거나 누락된 타임스탬프(timestamp) 형식입니다.')
  }

  // 2. mode
  const validModes = ['OFF', 'PAPER', 'LIVE']
  if (typeof raw.mode !== 'string' || !validModes.includes(raw.mode)) {
    errors.push(`모드(mode)는 'OFF', 'PAPER', 'LIVE' 중 하나여야 합니다. (입력값: ${String(raw.mode)})`)
  }

  // 3. source
  if (!isObject(raw.source)) {
    errors.push('데이터 출처 메타데이터(source) 객체가 누락되었습니다.')
  } else {
    const validKinds = ['synthetic', 'authoritative', 'local_snapshot']
    if (typeof raw.source.kind !== 'string' || !validKinds.includes(raw.source.kind)) {
      errors.push(`데이터 출처 종류(source.kind)가 올바르지 않습니다: ${String(raw.source.kind)}`)
    }
    if (typeof raw.source.label !== 'string' || !raw.source.label.trim()) {
      errors.push('데이터 출처 라벨(source.label) 문자열이 필요합니다.')
    }
  }

  // 4. portfolio
  if (!isObject(raw.portfolio)) {
    errors.push('포트폴리오 요약 정보(portfolio) 객체가 누락되었습니다.')
  } else {
    const p = raw.portfolio
    const numericFields = [
      'equity', 'cash', 'exposure', 'todayPnl', 'todayReturnPct',
      'totalPnl', 'totalReturnPct', 'realizedPnl', 'unrealizedPnl', 'fees',
    ]
    for (const field of numericFields) {
      if (!(field in p) || !isNumberOrNull(p[field])) {
        errors.push(`포트폴리오 '${field}' 필드는 숫자 또는 null이어야 합니다.`)
      }
    }
  }

  // 5. positions
  if (!Array.isArray(raw.positions)) {
    errors.push('보유 포지션 목록(positions)은 배열이어야 합니다.')
  } else {
    raw.positions.forEach((pos, idx) => {
      if (!isObject(pos)) {
        errors.push(`포지션 #${idx + 1} 항목이 객체가 아닙니다.`)
        return
      }
      if (typeof pos.id !== 'string' || !pos.id) errors.push(`포지션 #${idx + 1}: 식별자(id)가 필요합니다.`)
      if (typeof pos.asset !== 'string' || !pos.asset) errors.push(`포지션 #${idx + 1}: 자산명(asset)이 필요합니다.`)
      if (typeof pos.pair !== 'string' || !pos.pair) errors.push(`포지션 #${idx + 1}: 페어(pair)가 필요합니다.`)
      if (pos.side !== 'LONG') errors.push(`포지션 #${idx + 1}: side는 'LONG'이어야 합니다.`)
      const numFields = ['entry', 'current', 'quantity', 'exposure', 'pnl', 'pnlPct']
      for (const f of numFields) {
        if (!(f in pos) || !isNumberOrNull(pos[f])) {
          errors.push(`포지션 #${idx + 1}: '${f}' 필드는 숫자 또는 null이어야 합니다.`)
        }
      }
    })
  }

  // 6. recentTrades
  if (!Array.isArray(raw.recentTrades)) {
    errors.push('최근 거래 내역(recentTrades)은 배열이어야 합니다.')
  } else {
    raw.recentTrades.forEach((trade, idx) => {
      if (!isObject(trade)) {
        errors.push(`거래 내역 #${idx + 1} 항목이 객체가 아닙니다.`)
        return
      }
      if (typeof trade.id !== 'string' || !trade.id) errors.push(`거래 내역 #${idx + 1}: id가 필요합니다.`)
      if (typeof trade.asset !== 'string' || !trade.asset) errors.push(`거래 내역 #${idx + 1}: asset이 필요합니다.`)
      if (typeof trade.pair !== 'string' || !trade.pair) errors.push(`거래 내역 #${idx + 1}: pair가 필요합니다.`)
      const numFields = ['entry', 'exit', 'quantity', 'pnl', 'pnlPct', 'fee']
      for (const f of numFields) {
        if (!(f in trade) || !isNumberOrNull(trade[f])) {
          errors.push(`거래 내역 #${idx + 1}: '${f}' 필드는 숫자 또는 null이어야 합니다.`)
        }
      }
      if (typeof trade.closedAt !== 'string' || !Number.isFinite(Date.parse(trade.closedAt))) {
        errors.push(`거래 내역 #${idx + 1}: 체결 시각(closedAt) 형식이 올바르지 않습니다.`)
      }
    })
  }

  // 7. equityCurve
  if (!Array.isArray(raw.equityCurve)) {
    errors.push('자산 추이 곡선(equityCurve)은 배열이어야 합니다.')
  } else {
    raw.equityCurve.forEach((pt, idx) => {
      if (!isObject(pt)) {
        errors.push(`자산 추이 #${idx + 1} 항목이 객체가 아닙니다.`)
        return
      }
      if (typeof pt.timestamp !== 'string') errors.push(`자산 추이 #${idx + 1}: timestamp가 필요합니다.`)
      if (!isNumberOrNull(pt.equity)) errors.push(`자산 추이 #${idx + 1}: equity는 숫자 또는 null이어야 합니다.`)
    })
  }

  // 8. dailyPerformance
  if (!Array.isArray(raw.dailyPerformance)) {
    errors.push('일별 성과(dailyPerformance)는 배열이어야 합니다.')
  }

  // 9. botStatus
  if (!isObject(raw.botStatus)) {
    errors.push('봇 상태(botStatus) 객체가 누락되었습니다.')
  } else {
    const b = raw.botStatus
    if (!validModes.includes(String(b.mode))) errors.push(`봇 상태 mode가 유효하지 않습니다: ${String(b.mode)}`)
    const orderModes = ['DISABLED', 'PAPER', 'LIVE']
    if (!orderModes.includes(String(b.orderExecution))) {
      errors.push(`봇 상태 orderExecution이 유효하지 않습니다: ${String(b.orderExecution)}`)
    }
  }

  // 10. performance
  if (!isObject(raw.performance)) {
    errors.push('성과 분석 요약(performance) 객체가 누락되었습니다.')
  } else {
    const pf = raw.performance
    const pfFields = ['return7d', 'return30d', 'totalReturn', 'maxDrawdown', 'winRate', 'profitFactor', 'averageTrade']
    for (const f of pfFields) {
      if (!(f in pf) || !isNumberOrNull(pf[f])) {
        errors.push(`성과 요약 '${f}' 필드는 숫자 또는 null이어야 합니다.`)
      }
    }
  }

  // 11. today
  if (!isObject(raw.today)) {
    errors.push('오늘 요약(today) 객체가 누락되었습니다.')
  } else {
    const td = raw.today
    const tdFields = ['realizedPnl', 'unrealizedPnl', 'fees', 'trades', 'wins', 'losses', 'exposurePct']
    for (const f of tdFields) {
      if (!(f in td) || !isNumberOrNull(td[f])) {
        errors.push(`오늘 요약 '${f}' 필드는 숫자 또는 null이어야 합니다.`)
      }
    }
  }

  if (errors.length > 0) {
    return { valid: false, errors }
  }

  return {
    valid: true,
    data: raw as unknown as TradingSnapshot,
    errors: [],
  }
}

/**
 * Normalizes validated snapshot to guarantee internal field safety and calculations.
 */
export function normalizeTradingSnapshot(snapshot: TradingSnapshot): TradingSnapshot {
  return {
    ...snapshot,
    portfolio: {
      equity: snapshot.portfolio.equity,
      cash: snapshot.portfolio.cash,
      exposure: snapshot.portfolio.exposure,
      todayPnl: snapshot.portfolio.todayPnl,
      todayReturnPct: snapshot.portfolio.todayReturnPct,
      totalPnl: snapshot.portfolio.totalPnl,
      totalReturnPct: snapshot.portfolio.totalReturnPct,
      realizedPnl: snapshot.portfolio.realizedPnl,
      unrealizedPnl: snapshot.portfolio.unrealizedPnl,
      fees: snapshot.portfolio.fees,
    },
    positions: snapshot.positions.map(p => ({
      ...p,
      entry: p.entry,
      current: p.current,
      quantity: p.quantity,
      exposure: p.exposure,
      pnl: p.pnl,
      pnlPct: p.pnlPct,
    })),
    recentTrades: snapshot.recentTrades.map(t => ({
      ...t,
      pnl: t.pnl,
      pnlPct: t.pnlPct,
      fee: t.fee,
    })),
  }
}

/**
 * Export demo snapshot formatted as a JSON string for development & testing.
 * Strictly annotated with synthetic: true.
 */
export function exportDemoSnapshotJson(snapshot: TradingSnapshot): string {
  const exported = {
    ...snapshot,
    synthetic: true,
    exportedAt: new Date().toISOString(),
    notice: 'SYNTHETIC DEVELOPMENT DATA ONLY — NOT RESEARCH EVIDENCE OR LIVE METRICS',
  }
  return JSON.stringify(exported, null, 2)
}
