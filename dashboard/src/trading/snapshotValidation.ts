import type {
  BotStatus,
  PortfolioSummary,
  Position,
  Trade,
  TradingSnapshot,
} from './model'

export interface ValidationResult {
  valid: boolean
  data?: TradingSnapshot
  errors: string[]
}

function isObject(val: unknown): val is Record<string, unknown> {
  return typeof val === 'object' && val !== null && !Array.isArray(val)
}

function isFiniteNumber(val: unknown): val is number {
  return typeof val === 'number' && Number.isFinite(val)
}

function isNumberOrNull(val: unknown): val is number | null {
  return val === null || isFiniteNumber(val)
}

function isStringOrNull(val: unknown): val is string | null {
  return val === null || typeof val === 'string'
}

function isValidTimestamp(val: unknown): val is string {
  if (typeof val !== 'string' || !val.trim()) return false
  // Require ISO 8601 with explicit timezone (Z or [+-]HH:MM or [+-]HHMM)
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})$/.test(val)) return false
  const parsed = Date.parse(val)
  return Number.isFinite(parsed)
}

function isValidDateYmd(val: unknown): val is string {
  if (typeof val !== 'string') return false
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(val)
  if (!match) return false
  const y = parseInt(match[1], 10)
  const m = parseInt(match[2], 10)
  const d = parseInt(match[3], 10)
  if (m < 1 || m > 12 || d < 1 || d > 31) return false
  const date = new Date(Date.UTC(y, m - 1, d))
  return date.getUTCFullYear() === y && date.getUTCMonth() === m - 1 && date.getUTCDate() === d
}

/**
 * Full structural validator for PortfolioSummary.
 */
export function validatePortfolioSummary(val: unknown, prefix = 'portfolio'): string[] {
  const errors: string[] = []
  if (!isObject(val)) {
    return [`${prefix}: 객체 형식이어야 합니다.`]
  }

  const fields: (keyof PortfolioSummary)[] = [
    'equity', 'cash', 'exposure', 'todayPnl', 'todayReturnPct',
    'totalPnl', 'totalReturnPct', 'realizedPnl', 'unrealizedPnl', 'fees',
  ]
  for (const f of fields) {
    if (!(f in val) || !isNumberOrNull(val[f])) {
      errors.push(`${prefix}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
    }
  }
  return errors
}

/**
 * Full structural validator for Position array.
 */
export function validatePositions(val: unknown, prefix = 'positions'): string[] {
  const errors: string[] = []
  if (!Array.isArray(val)) {
    return [`${prefix}: 배열 형식이어야 합니다.`]
  }

  val.forEach((pos, idx) => {
    const pfx = `${prefix}[${idx}]`
    if (!isObject(pos)) {
      errors.push(`${pfx}: 객체 형식이어야 합니다.`)
      return
    }
    if (typeof pos.id !== 'string' || !pos.id.trim()) errors.push(`${pfx}.id: 비어있지 않은 문자열이어야 합니다.`)
    if (typeof pos.asset !== 'string' || !pos.asset.trim()) errors.push(`${pfx}.asset: 비어있지 않은 문자열이어야 합니다.`)
    if (typeof pos.name !== 'string') errors.push(`${pfx}.name: 문자열이어야 합니다.`)
    if (typeof pos.pair !== 'string' || !pos.pair.trim()) errors.push(`${pfx}.pair: 비어있지 않은 문자열이어야 합니다.`)
    if (pos.side !== 'LONG') errors.push(`${pfx}.side: 'LONG'이어야 합니다.`)

    const numFields: (keyof Position)[] = ['entry', 'current', 'quantity', 'exposure', 'pnl', 'pnlPct', 'entryFee']
    for (const f of numFields) {
      if (!(f in pos) || !isNumberOrNull(pos[f])) {
        errors.push(`${pfx}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
      }
    }

    if (!isValidTimestamp(pos.openedAt)) {
      errors.push(`${pfx}.openedAt: 유효한 ISO 8601 타임스탬프 형식이어야 합니다.`)
    }
    if (!isStringOrNull(pos.strategy)) {
      errors.push(`${pfx}.strategy: 문자열 또는 null이어야 합니다.`)
    }
  })

  return errors
}

/**
 * Full structural validator for Trade array.
 */
export function validateTrades(val: unknown, prefix = 'recentTrades'): string[] {
  const errors: string[] = []
  if (!Array.isArray(val)) {
    return [`${prefix}: 배열 형식이어야 합니다.`]
  }

  val.forEach((trade, idx) => {
    const pfx = `${prefix}[${idx}]`
    if (!isObject(trade)) {
      errors.push(`${pfx}: 객체 형식이어야 합니다.`)
      return
    }
    if (typeof trade.id !== 'string' || !trade.id.trim()) errors.push(`${pfx}.id: 비어있지 않은 문자열이어야 합니다.`)
    if (typeof trade.asset !== 'string' || !trade.asset.trim()) errors.push(`${pfx}.asset: 비어있지 않은 문자열이어야 합니다.`)
    if (typeof trade.pair !== 'string' || !trade.pair.trim()) errors.push(`${pfx}.pair: 비어있지 않은 문자열이어야 합니다.`)
    if (trade.side !== 'LONG') errors.push(`${pfx}.side: 'LONG'이어야 합니다.`)

    const numFields: (keyof Trade)[] = ['entry', 'exit', 'quantity', 'pnl', 'pnlPct', 'fee']
    for (const f of numFields) {
      if (!(f in trade) || !isNumberOrNull(trade[f])) {
        errors.push(`${pfx}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
      }
    }

    const hasOpened = isValidTimestamp(trade.openedAt)
    const hasClosed = isValidTimestamp(trade.closedAt)
    if (!hasOpened) errors.push(`${pfx}.openedAt: 유효한 ISO 8601 타임스탬프 형식이어야 합니다.`)
    if (!hasClosed) errors.push(`${pfx}.closedAt: 유효한 ISO 8601 타임스탬프 형식이어야 합니다.`)

    // Semantic sanity: openedAt <= closedAt
    if (hasOpened && hasClosed && Date.parse(trade.openedAt as string) > Date.parse(trade.closedAt as string)) {
      errors.push(`${pfx}: 진입 시각(openedAt)이 청산 시각(closedAt)보다 미래일 수 없습니다.`)
    }

    if (!isStringOrNull(trade.exitReason)) {
      errors.push(`${pfx}.exitReason: 문자열 또는 null이어야 합니다.`)
    }
  })

  return errors
}

/**
 * Full structural validator for BotStatus.
 */
export function validateBotStatus(val: unknown, prefix = 'botStatus'): string[] {
  const errors: string[] = []
  if (!isObject(val)) {
    return [`${prefix}: 객체 형식이어야 합니다.`]
  }

  const validModes = ['OFF', 'PAPER', 'LIVE']
  if (typeof val.mode !== 'string' || !validModes.includes(val.mode)) {
    errors.push(`${prefix}.mode: 'OFF', 'PAPER', 'LIVE' 중 하나여야 합니다.`)
  }
  if (!isStringOrNull(val.strategy)) errors.push(`${prefix}.strategy: 문자열 또는 null이어야 합니다.`)

  const validMarket = ['PENDING', 'READY']
  if (typeof val.marketData !== 'string' || !validMarket.includes(val.marketData)) {
    errors.push(`${prefix}.marketData: 'PENDING', 'READY' 중 하나여야 합니다.`)
  }

  const validExecution = ['DISABLED', 'PAPER', 'LIVE']
  if (typeof val.orderExecution !== 'string' || !validExecution.includes(val.orderExecution)) {
    errors.push(`${prefix}.orderExecution: 'DISABLED', 'PAPER', 'LIVE' 중 하나여야 합니다.`)
  }

  const validGuard = ['LOCKED', 'ACTIVE']
  if (typeof val.riskGuard !== 'string' || !validGuard.includes(val.riskGuard)) {
    errors.push(`${prefix}.riskGuard: 'LOCKED', 'ACTIVE' 중 하나여야 합니다.`)
  }

  if (val.lastActivity !== null && !isValidTimestamp(val.lastActivity)) {
    errors.push(`${prefix}.lastActivity: 유효한 ISO 8601 타임스탬프 또는 null이어야 합니다.`)
  }

  const nonNegFields: (keyof BotStatus)[] = ['uptimeSeconds', 'todayTrades', 'errors']
  for (const f of nonNegFields) {
    if (!(f in val) || !isNumberOrNull(val[f])) {
      errors.push(`${prefix}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
    } else if (val[f] !== null && (val[f] as number) < 0) {
      errors.push(`${prefix}.${f}: 음수일 수 없습니다. (값: ${val[f]})`)
    }
  }

  return errors
}

/**
 * Full structural validator for Performance block.
 */
export function validatePerformance(val: unknown, prefix = 'performance'): string[] {
  const errors: string[] = []
  if (!isObject(val)) {
    return [`${prefix}: 객체 형식이어야 합니다.`]
  }

  const fields = ['return7d', 'return30d', 'totalReturn', 'maxDrawdown', 'winRate', 'profitFactor', 'averageTrade']
  for (const f of fields) {
    if (!(f in val) || !isNumberOrNull(val[f])) {
      errors.push(`${prefix}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
    }
  }
  return errors
}

/**
 * Full structural validator for Today block.
 */
export function validateToday(val: unknown, prefix = 'today'): string[] {
  const errors: string[] = []
  if (!isObject(val)) {
    return [`${prefix}: 객체 형식이어야 합니다.`]
  }

  const numFields = ['realizedPnl', 'unrealizedPnl', 'fees', 'exposurePct']
  for (const f of numFields) {
    if (!(f in val) || !isNumberOrNull(val[f])) {
      errors.push(`${prefix}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
    }
  }

  const nonNegFields = ['trades', 'wins', 'losses']
  for (const f of nonNegFields) {
    if (!(f in val) || !isNumberOrNull(val[f])) {
      errors.push(`${prefix}.${f}: 유한한 숫자 또는 null이어야 합니다.`)
    } else if (val[f] !== null && (val[f] as number) < 0) {
      errors.push(`${prefix}.${f}: 음수일 수 없습니다. (값: ${val[f]})`)
    }
  }

  return errors
}

/**
 * Full structural validator for DailyBaseline.
 */
export function validateDailyBaseline(val: unknown, prefix = 'dailyBaseline'): string[] {
  const errors: string[] = []
  if (val === null) return errors

  if (!isObject(val)) {
    return [`${prefix}: 객체 또는 null이어야 합니다.`]
  }

  if (!('equity' in val) || !isNumberOrNull(val.equity)) {
    errors.push(`${prefix}.equity: 유한한 숫자 또는 null이어야 합니다.`)
  }
  if (!('netCashFlow' in val) || !isNumberOrNull(val.netCashFlow)) {
    errors.push(`${prefix}.netCashFlow: 유한한 숫자 또는 null이어야 합니다.`)
  }
  if (!isValidDateYmd(val.tradingDay)) {
    errors.push(`${prefix}.tradingDay: 유효한 YYYY-MM-DD 날짜 형식이어야 합니다.`)
  }
  if (val.timeZone !== 'Asia/Seoul') {
    errors.push(`${prefix}.timeZone: 반드시 'Asia/Seoul'이어야 합니다.`)
  }

  return errors
}

/**
 * Validates raw JSON payload against TradingSnapshot schema.
 * Strictly verifies every single field, sub-object, array item, and semantic bounds.
 * Never replaces missing/invalid fields with zero. Returns descriptive Korean errors.
 */
export function validateTradingSnapshot(raw: unknown): ValidationResult {
  const errors: string[] = []

  if (!isObject(raw)) {
    return { valid: false, errors: ['스냅샷 최상위 데이터가 올바른 JSON 객체가 아닙니다.'] }
  }

  // 0. schemaVersion
  if (!('schemaVersion' in raw)) {
    errors.push('schemaVersion: 필드가 누락되었습니다 (지원 버전: 1).')
  } else if (raw.schemaVersion !== 1) {
    errors.push(`schemaVersion: 지원하지 않는 스키마 버전입니다. (지원 버전: 1, 입력값: ${String(raw.schemaVersion)})`)
  }

  // 1. timestamp
  if (!isValidTimestamp(raw.timestamp)) {
    errors.push('timestamp: 유효하지 않거나 누락된 ISO 8601 타임스탬프 형식입니다.')
  }

  // 2. mode
  const validModes = ['OFF', 'PAPER', 'LIVE']
  if (typeof raw.mode !== 'string' || !validModes.includes(raw.mode)) {
    errors.push(`mode: 'OFF', 'PAPER', 'LIVE' 중 하나여야 합니다. (입력값: ${String(raw.mode)})`)
  }

  // 3. source
  if (!isObject(raw.source)) {
    errors.push('source: 데이터 출처 메타데이터 객체가 누락되었습니다.')
  } else {
    const validKinds = ['synthetic', 'authoritative', 'local_snapshot']
    if (typeof raw.source.kind !== 'string' || !validKinds.includes(raw.source.kind)) {
      errors.push(`source.kind: 'synthetic', 'authoritative', 'local_snapshot' 중 하나여야 합니다. (입력값: ${String(raw.source.kind)})`)
    }
    if (typeof raw.source.label !== 'string' || !raw.source.label.trim()) {
      errors.push('source.label: 비어있지 않은 문자열이어야 합니다.')
    }
  }

  // 4. portfolio
  errors.push(...validatePortfolioSummary(raw.portfolio, 'portfolio'))

  // 5. positions
  errors.push(...validatePositions(raw.positions, 'positions'))

  // 6. recentTrades
  errors.push(...validateTrades(raw.recentTrades, 'recentTrades'))

  // 7. equityCurve
  if (!Array.isArray(raw.equityCurve)) {
    errors.push('equityCurve: 배열 형식이어야 합니다.')
  } else {
    raw.equityCurve.forEach((pt, idx) => {
      const pfx = `equityCurve[${idx}]`
      if (!isObject(pt)) {
        errors.push(`${pfx}: 객체 형식이어야 합니다.`)
        return
      }
      if (!isValidTimestamp(pt.timestamp)) errors.push(`${pfx}.timestamp: 유효한 ISO 8601 타임스탬프여야 합니다.`)
      if (!('equity' in pt) || !isNumberOrNull(pt.equity)) errors.push(`${pfx}.equity: 유한한 숫자 또는 null이어야 합니다.`)
      if (!('returnPct' in pt) || !isNumberOrNull(pt.returnPct)) errors.push(`${pfx}.returnPct: 유한한 숫자 또는 null이어야 합니다.`)
      if (!('drawdownPct' in pt) || !isNumberOrNull(pt.drawdownPct)) errors.push(`${pfx}.drawdownPct: 유한한 숫자 또는 null이어야 합니다.`)
    })
  }

  // 8. dailyPerformance
  if (!Array.isArray(raw.dailyPerformance)) {
    errors.push('dailyPerformance: 배열 형식이어야 합니다.')
  } else {
    raw.dailyPerformance.forEach((dp, idx) => {
      const pfx = `dailyPerformance[${idx}]`
      if (!isObject(dp)) {
        errors.push(`${pfx}: 객체 형식이어야 합니다.`)
        return
      }
      if (!isValidDateYmd(dp.date)) errors.push(`${pfx}.date: 유효한 YYYY-MM-DD 날짜 형식이어야 합니다.`)
      if (!('pnl' in dp) || !isNumberOrNull(dp.pnl)) errors.push(`${pfx}.pnl: 유한한 숫자 또는 null이어야 합니다.`)
      if (!('returnPct' in dp) || !isNumberOrNull(dp.returnPct)) errors.push(`${pfx}.returnPct: 유한한 숫자 또는 null이어야 합니다.`)
    })
  }

  // 9. botStatus
  errors.push(...validateBotStatus(raw.botStatus, 'botStatus'))

  // 10. performance
  errors.push(...validatePerformance(raw.performance, 'performance'))

  // 11. today
  errors.push(...validateToday(raw.today, 'today'))

  // 12. dailyBaseline
  if (!('dailyBaseline' in raw)) {
    errors.push('dailyBaseline: 필드가 누락되었습니다 (DailyBaseline 객체 또는 null 필요).')
  } else {
    errors.push(...validateDailyBaseline(raw.dailyBaseline, 'dailyBaseline'))
  }

  if (errors.length > 0) {
    return { valid: false, errors }
  }

  // Only after every single structural property has been verified, return the strongly-typed snapshot
  const s = raw as unknown as TradingSnapshot
  return {
    valid: true,
    data: s,
    errors: [],
  }
}

/**
 * Normalizes validated snapshot to guarantee internal field safety and calculations.
 */
export function normalizeTradingSnapshot(snapshot: TradingSnapshot): TradingSnapshot {
  return {
    schemaVersion: snapshot.schemaVersion ?? 1,
    timestamp: snapshot.timestamp,
    mode: snapshot.mode,
    source: {
      kind: snapshot.source.kind,
      label: snapshot.source.label,
    },
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
      id: p.id,
      asset: p.asset,
      name: p.name,
      pair: p.pair,
      side: p.side,
      entry: p.entry,
      current: p.current,
      quantity: p.quantity,
      exposure: p.exposure,
      pnl: p.pnl,
      pnlPct: p.pnlPct,
      entryFee: p.entryFee,
      openedAt: p.openedAt,
      strategy: p.strategy,
    })),
    recentTrades: snapshot.recentTrades.map(t => ({
      id: t.id,
      asset: t.asset,
      pair: t.pair,
      side: t.side,
      entry: t.entry,
      exit: t.exit,
      quantity: t.quantity,
      pnl: t.pnl,
      pnlPct: t.pnlPct,
      fee: t.fee,
      openedAt: t.openedAt,
      closedAt: t.closedAt,
      exitReason: t.exitReason,
    })),
    equityCurve: snapshot.equityCurve.map(pt => ({
      timestamp: pt.timestamp,
      equity: pt.equity,
      returnPct: pt.returnPct,
      drawdownPct: pt.drawdownPct,
    })),
    dailyPerformance: snapshot.dailyPerformance.map(dp => ({
      date: dp.date,
      pnl: dp.pnl,
      returnPct: dp.returnPct,
    })),
    botStatus: {
      mode: snapshot.botStatus.mode,
      strategy: snapshot.botStatus.strategy,
      marketData: snapshot.botStatus.marketData,
      orderExecution: snapshot.botStatus.orderExecution,
      riskGuard: snapshot.botStatus.riskGuard,
      lastActivity: snapshot.botStatus.lastActivity,
      uptimeSeconds: snapshot.botStatus.uptimeSeconds,
      todayTrades: snapshot.botStatus.todayTrades,
      errors: snapshot.botStatus.errors,
    },
    performance: {
      return7d: snapshot.performance.return7d,
      return30d: snapshot.performance.return30d,
      totalReturn: snapshot.performance.totalReturn,
      maxDrawdown: snapshot.performance.maxDrawdown,
      winRate: snapshot.performance.winRate,
      profitFactor: snapshot.performance.profitFactor,
      averageTrade: snapshot.performance.averageTrade,
    },
    today: {
      realizedPnl: snapshot.today.realizedPnl,
      unrealizedPnl: snapshot.today.unrealizedPnl,
      fees: snapshot.today.fees,
      trades: snapshot.today.trades,
      wins: snapshot.today.wins,
      losses: snapshot.today.losses,
      exposurePct: snapshot.today.exposurePct,
    },
    dailyBaseline: snapshot.dailyBaseline ? {
      equity: snapshot.dailyBaseline.equity,
      netCashFlow: snapshot.dailyBaseline.netCashFlow,
      tradingDay: snapshot.dailyBaseline.tradingDay,
      timeZone: 'Asia/Seoul',
    } : null,
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
