/** Trading values come from an adapter; null always means unknown, never zero. */
export interface PortfolioSummary {
  equity: number | null
  cash: number | null
  exposure: number | null
  todayPnl: number | null
  todayReturnPct: number | null
  totalPnl: number | null
  totalReturnPct: number | null
  realizedPnl: number | null
  unrealizedPnl: number | null
  /** Paid fees are already included in net PnL. Do not subtract them again. */
  fees: number | null
}

export interface Position {
  id: string
  asset: string
  name: string
  pair: string
  side: 'LONG'
  entry: number | null
  current: number | null
  quantity: number | null
  exposure: number | null
  /** Net of the opening fee; an unexecuted closing fee is not assumed. */
  pnl: number | null
  pnlPct: number | null
  entryFee: number | null
  openedAt: string
  strategy: string | null
}

export interface Trade {
  id: string
  asset: string
  pair: string
  side: 'LONG'
  entry: number | null
  exit: number | null
  quantity: number | null
  /** Realized PnL after both opening and closing fees. */
  pnl: number | null
  pnlPct: number | null
  fee: number | null
  openedAt: string
  closedAt: string
  exitReason: string | null
}

export interface EquityPoint {
  timestamp: string
  equity: number | null
  returnPct: number | null
  drawdownPct: number | null
}

export interface DailyPerformance {
  date: string
  pnl: number | null
  returnPct: number | null
}

export interface BotStatus {
  mode: 'OFF' | 'PAPER' | 'LIVE'
  strategy: string | null
  marketData: 'PENDING' | 'READY'
  orderExecution: 'DISABLED' | 'PAPER' | 'LIVE'
  riskGuard: 'LOCKED' | 'ACTIVE'
  lastActivity: string | null
  uptimeSeconds: number | null
  todayTrades: number | null
  errors: number | null
}

/**
 * An authoritative adapter must provide the current KST trading day's opening
 * equity and COMPLETE net external cash flow since that boundary. A deposit is
 * positive; a withdrawal is negative. Missing or stale baselines cannot produce
 * Today PnL/Return. This is a cash-flow-adjusted opening-equity return, not TWR.
 */
export interface DailyBaseline {
  equity: number | null
  netCashFlow: number | null
  tradingDay: string
  timeZone: 'Asia/Seoul'
}

export interface TradingSnapshot {
  schemaVersion: 1
  timestamp: string
  mode: 'OFF' | 'PAPER' | 'LIVE'
  source: { kind: 'synthetic' | 'authoritative' | 'local_snapshot'; label: string }
  portfolio: PortfolioSummary
  positions: Position[]
  recentTrades: Trade[]
  equityCurve: EquityPoint[]
  dailyPerformance: DailyPerformance[]
  botStatus: BotStatus
  performance: {
    return7d: number | null
    return30d: number | null
    totalReturn: number | null
    maxDrawdown: number | null
    winRate: number | null
    profitFactor: number | null
    averageTrade: number | null
  }
  today: {
    realizedPnl: number | null
    /** Change in net unrealized PnL since the daily baseline, not total open PnL. */
    unrealizedPnl: number | null
    fees: number | null
    trades: number | null
    wins: number | null
    losses: number | null
    exposurePct: number | null
  }
  dailyBaseline: DailyBaseline | null
}

export type TradingDataStatus =
  | 'LOADING'
  | 'NO_DATA'
  | 'SYNTHETIC_DEMO'
  | 'LOCAL_SNAPSHOT'
  | 'READ_ONLY_API'
  | 'REAL_DATA'
  | 'ERROR'

export type TradingDataState =
  | { status: 'LOADING' | 'NO_DATA' | 'ERROR'; snapshot?: never; error?: string; isStale?: boolean }
  | {
      status: 'SYNTHETIC_DEMO' | 'LOCAL_SNAPSHOT' | 'READ_ONLY_API' | 'REAL_DATA'
      snapshot: TradingSnapshot
      error?: string
      isStale?: boolean
    }

export const NO_TRADING_DATA: TradingDataState = { status: 'NO_DATA' }

export function tradingDayInSeoul(timestamp: string): string | null {
  const time = Date.parse(timestamp)
  return Number.isFinite(time)
    ? new Date(time + 9 * 60 * 60 * 1000).toISOString().slice(0, 10)
    : null
}

export function calculateTodayMetrics(
  equity: number | null,
  baseline: DailyBaseline | null,
  timestamp: string,
): { todayPnl: number | null; todayReturnPct: number | null } {
  if (
    equity === null || !Number.isFinite(equity) ||
    !baseline || baseline.timeZone !== 'Asia/Seoul' ||
    baseline.equity === null || !Number.isFinite(baseline.equity) || baseline.equity <= 0 ||
    baseline.netCashFlow === null || !Number.isFinite(baseline.netCashFlow) ||
    baseline.tradingDay !== tradingDayInSeoul(timestamp)
  ) return { todayPnl: null, todayReturnPct: null }

  const todayPnl = equity - baseline.equity - baseline.netCashFlow
  return { todayPnl, todayReturnPct: todayPnl / baseline.equity * 100 }
}
