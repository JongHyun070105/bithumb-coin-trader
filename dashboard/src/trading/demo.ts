import {
  calculateTodayMetrics,
  tradingDayInSeoul,
  type DailyPerformance,
  type EquityPoint,
  type Position,
  type Trade,
  type TradingSnapshot,
} from './model'

/**
 * 고정된 합성 데모 시나리오 — 사용자 지정 invariants:
 *
 * 시작 자본:      ₩10,000,000
 * 현재 자본:      ₩10,843,200  (= 시작 + 총 PnL)
 * 총 PnL:         +₩843,200    (+8.432%)
 * 오늘 PnL:       +₩137,500    (+1.28%)
 *   오늘 실현:    +₩91,000
 *   오늘 미실현:  +₩52,700     (= BTC 32,400 + ETH 14,800 + SOL 5,500)
 *   오늘 수수료:  -₩6,200
 *   불변식:       91,000 + 52,700 - 6,200 = 137,500 ✓
 * 오픈 포지션:    3개 (BTC, ETH, SOL)
 * 오늘 거래:      5건 (3승 2패)
 * 노출 비율:      ~42.7% (42.69%)
 */
export const DEMO_TIMESTAMP = '2026-09-08T07:30:00.000Z'
export const DEMO_STARTING_CAPITAL = 10_000_000

// ── 오늘 KST 경계 ──────────────────────────────────────────────────────────
const TODAY_KST_START = Date.parse('2026-09-08T00:00:00+09:00') // 2026-09-07T15:00:00.000Z
const END = Date.parse(DEMO_TIMESTAMP)
const DAY = 86_400_000
const iso = (ms: number) => new Date(ms).toISOString()
const kstMs = (daysAgo: number, hour = 10) =>
  TODAY_KST_START - daysAgo * DAY + hour * 3_600_000

// ── 사용자 지정 상수 ────────────────────────────────────────────────────────
const TOTAL_PNL        = 843_200
const CURRENT_EQUITY   = DEMO_STARTING_CAPITAL + TOTAL_PNL   // 10,843,200
const TODAY_REALIZED   =  91_000
const TODAY_UNREALIZED =  52_700   // BTC 32,400 + ETH 14,800 + SOL 5,500
const TODAY_FEES       =   6_200
// 불변식 검증: TODAY_REALIZED + TODAY_UNREALIZED - TODAY_FEES = 137,500
const TODAY_PNL        = TODAY_REALIZED + TODAY_UNREALIZED - TODAY_FEES  // 137,500

// ── 오픈 포지션 (3개, 미실현 PnL 합계 = 52,700, 정확한 산술 일치) ────────────
function makePositions(): Position[] {
  const specs = [
    { asset: 'BTC', name: '비트코인', entry: 83_800_000, current: 84_921_900, qty: 0.03, entryFee: 1_257, pnl: 32_400, daysAgo: 12 },
    { asset: 'ETH', name: '이더리움', entry:  3_400_000, current:  3_438_700, qty: 0.40, entryFee:   680, pnl: 14_800, daysAgo:  8 },
    { asset: 'SOL', name: '솔라나',   entry:    140_000, current:    141_170, qty: 5.00, entryFee:   350, pnl:  5_500, daysAgo:  5 },
  ] as const
  return specs.map((s, i) => {
    const cost     = Math.round(s.entry * s.qty)
    const exposure = Math.round(s.current * s.qty)
    const pnlPct   = (s.pnl / (cost + s.entryFee)) * 100
    return {
      id: `demo-pos-${i + 1}`, asset: s.asset, name: s.name,
      pair: `${s.asset}/KRW`, side: 'LONG' as const,
      entry: s.entry, current: s.current, quantity: s.qty, entryFee: s.entryFee,
      pnl: s.pnl, pnlPct, exposure,
      openedAt: iso(kstMs(s.daysAgo)),
      strategy: '합성 추세 미리보기',
    }
  })
}

// ── 거래 내역 (오늘 5건: 3승+2패, gross-fee=pnl 정확 성립) + 과거 7건 ───────
function makeTrades(): Trade[] {
  // 오늘 체결 거래 5건
  const today = [
    { asset: 'BTC', pair: 'BTC/KRW', entry: 83_000_000, exit: 84_000_000, qty: 0.05, fee: 2_100, openH: 1, closeH: 4, reason: '목표가 도달' },
    { asset: 'ETH', pair: 'ETH/KRW', entry:  3_300_000, exit:  3_360_000, qty: 0.60, fee: 1_800, openH: 2, closeH: 5, reason: '목표가 도달' },
    { asset: 'SOL', pair: 'SOL/KRW', entry:    170_000, exit:    172_500, qty: 10.0, fee: 1_300, openH: 3, closeH: 6, reason: '목표가 도달' },
    { asset: 'ETH', pair: 'ETH/KRW', entry:  3_350_000, exit:  3_315_000, qty: 0.20, fee:   600, openH: 0, closeH: 2, reason: '손절매' },
    { asset: 'BTC', pair: 'BTC/KRW', entry: 84_200_000, exit: 83_860_000, qty: 0.02, fee:   400, openH: 5, closeH: 7, reason: '손절매' },
  ]
  // 과거 거래 (지난 30일 내 7건, sum(pnl) = 699,500)
  const history = [
    { asset: 'BTC', pair: 'BTC/KRW', entry: 76_000_000, exit: 80_000_000, qty: 0.05, fee: 4_000, daysAgo: 28, openH:  9, closeH: 14, reason: '목표가 도달' },
    { asset: 'ETH', pair: 'ETH/KRW', entry:  3_000_000, exit:  3_250_000, qty: 1.00, fee: 1_500, daysAgo: 22, openH: 10, closeH: 15, reason: '목표가 도달' },
    { asset: 'SOL', pair: 'SOL/KRW', entry:    150_000, exit:    167_882, qty: 10.0, fee: 1_650, daysAgo: 18, openH:  9, closeH: 13, reason: '목표가 도달' },
    { asset: 'XRP', pair: 'XRP/KRW', entry:        720, exit:        705, qty: 2000, fee:   720, daysAgo: 14, openH: 11, closeH: 16, reason: '손절매' },
    { asset: 'BTC', pair: 'BTC/KRW', entry: 81_000_000, exit: 82_500_000, qty: 0.04, fee: 1_600, daysAgo: 10, openH:  8, closeH: 12, reason: '목표가 도달' },
    { asset: 'ETH', pair: 'ETH/KRW', entry:  3_300_000, exit:  3_260_000, qty: 0.50, fee:   800, daysAgo:  6, openH:  9, closeH: 14, reason: '손절매' },
    { asset: 'SOL', pair: 'SOL/KRW', entry:    165_000, exit:    177_000, qty: 6.00, fee: 1_050, daysAgo:  3, openH: 10, closeH: 15, reason: '목표가 도달' },
  ]
  const all = [
    ...today.map((t, i) => {
      const gross = Math.round((t.exit - t.entry) * t.qty)
      const pnl = gross - t.fee
      return {
        id: `demo-trade-${String(i + 1).padStart(2, '0')}`,
        asset: t.asset, pair: t.pair, side: 'LONG' as const,
        entry: t.entry, exit: t.exit, quantity: t.qty,
        pnl, fee: t.fee, pnlPct: (pnl / (t.entry * t.qty)) * 100,
        openedAt: iso(kstMs(0, t.openH)), closedAt: iso(kstMs(0, t.closeH)),
        exitReason: t.reason,
      }
    }),
    ...history.map((t, i) => {
      const gross = Math.round((t.exit - t.entry) * t.qty)
      const pnl = gross - t.fee
      return {
        id: `demo-trade-${String(i + 6).padStart(2, '0')}`,
        asset: t.asset, pair: t.pair, side: 'LONG' as const,
        entry: t.entry, exit: t.exit, quantity: t.qty,
        pnl, fee: t.fee, pnlPct: (pnl / (t.entry * t.qty)) * 100,
        openedAt: iso(kstMs(t.daysAgo, t.openH)), closedAt: iso(kstMs(t.daysAgo, t.closeH)),
        exitReason: t.reason,
      }
    }),
  ]
  return all.sort((a, b) => Date.parse(b.closedAt) - Date.parse(a.closedAt))
}

export function createDemoSnapshot(): TradingSnapshot {
  const positions = makePositions()
  const trades    = makeTrades()
  const todayDay  = tradingDayInSeoul(DEMO_TIMESTAMP)!

  // ── 오늘 거래 집계 ────────────────────────────────────────────────────────
  const todayTrades = trades.filter(t => tradingDayInSeoul(t.closedAt) === todayDay)

  // ── 자본 집계 ─────────────────────────────────────────────────────────────
  const exposure    = Math.round(positions.reduce((s, p) => s + (p.exposure ?? 0), 0))
  const cash        = CURRENT_EQUITY - exposure
  const totalReal   = Math.round(trades.reduce((s, t) => s + t.pnl!, 0))
  const totalFees   = Math.round(trades.reduce((s, t) => s + t.fee!, 0)) + Math.round(positions.reduce((s, p) => s + (p.entryFee ?? 0), 0))

  // ── 일별 자본 곡선 (30일 결정론적, END에서 CURRENT_EQUITY 보장) ──────────
  const DAYS = 30
  const dailyPerformance: DailyPerformance[] = []
  const equityCurve: EquityPoint[] = []

  // 과거 29일간의 일별 PnL 분포 (합계 = 705,700)
  const historyPnls = [
    63772, 71700, 57300, 27727, -1093, -13939, -5333, 17353, 38552, 44112,
    29994, 4782, -15528, -17291, 2392, 33750, 60075, 67653, 53879, 28660,
    8268, 5378, 21361, 45476, 61487, 57652, 34025, 2631, -79095,
  ] // sum = 705,700


  let rollingEquity = DEMO_STARTING_CAPITAL
  let peak = DEMO_STARTING_CAPITAL

  // d = -30 시작점
  equityCurve.push({
    timestamp: iso(TODAY_KST_START - DAYS * DAY),
    equity: DEMO_STARTING_CAPITAL,
    returnPct: 0,
    drawdownPct: 0,
  })

  // d = -29 부터 d = -1 까지의 일별 포인트
  for (let i = 0; i < historyPnls.length; i++) {
    const daysAgo = (DAYS - 1) - i // 29부터 1까지
    const pnl = historyPnls[i]
    const prevEquity = rollingEquity
    rollingEquity += pnl
    peak = Math.max(peak, rollingEquity)
    const returnPct = (pnl / prevEquity) * 100
    const ts = TODAY_KST_START - daysAgo * DAY

    dailyPerformance.push({
      date: tradingDayInSeoul(iso(ts + 12 * 3_600_000))!,
      pnl,
      returnPct,
    })

    equityCurve.push({
      timestamp: iso(ts),
      equity: rollingEquity,
      returnPct: (rollingEquity / DEMO_STARTING_CAPITAL - 1) * 100,
      drawdownPct: (rollingEquity / peak - 1) * 100,
    })
  }

  // d = 0 (오늘 00:00 KST = 2026-09-07T15:00:00.000Z 기준선)
  // rollingEquity는 정확히 10,705,700
  const baselineEquity = rollingEquity // 10,705,700
  peak = Math.max(peak, baselineEquity)
  equityCurve.push({
    timestamp: iso(TODAY_KST_START),
    equity: baselineEquity,
    returnPct: (baselineEquity / DEMO_STARTING_CAPITAL - 1) * 100,
    drawdownPct: (baselineEquity / peak - 1) * 100,
  })

  // 오늘 일별 PnL 및 현재 시점 (16:30 KST = END)
  dailyPerformance.push({
    date: todayDay,
    pnl: TODAY_PNL,
    returnPct: (TODAY_PNL / baselineEquity) * 100,
  })

  peak = Math.max(peak, CURRENT_EQUITY)
  equityCurve.push({
    timestamp: iso(END),
    equity: CURRENT_EQUITY,
    returnPct: (CURRENT_EQUITY / DEMO_STARTING_CAPITAL - 1) * 100,
    drawdownPct: (CURRENT_EQUITY / peak - 1) * 100,
  })

  // ── 성과 지표 ─────────────────────────────────────────────────────────────
  const wins   = trades.filter(t => t.pnl! > 0)
  const losses = trades.filter(t => t.pnl! < 0)
  const profit = wins.reduce((s, t) => s + t.pnl!, 0)
  const loss   = -losses.reduce((s, t) => s + t.pnl!, 0)
  const totalReturn = (TOTAL_PNL / DEMO_STARTING_CAPITAL) * 100  // 8.432%
  const maxDrawdown = Math.min(...equityCurve.map(p => p.drawdownPct!))

  // ── 일별 베이스라인 ───────────────────────────────────────────────────────
  const dailyBaseline = {
    equity: baselineEquity,
    netCashFlow: 0,
    tradingDay: todayDay,
    timeZone: 'Asia/Seoul' as const,
  }
  const todayMetrics = calculateTodayMetrics(CURRENT_EQUITY, dailyBaseline, DEMO_TIMESTAMP)

  return {
    timestamp: DEMO_TIMESTAMP, mode: 'OFF',
    source: { kind: 'synthetic', label: 'DEMO DATA · 합성 미리보기' },
    portfolio: {
      equity: CURRENT_EQUITY, cash, exposure,
      todayPnl:       todayMetrics.todayPnl ?? TODAY_PNL,
      todayReturnPct: todayMetrics.todayReturnPct ?? (TODAY_PNL / baselineEquity * 100),
      totalPnl:    TOTAL_PNL, totalReturnPct: totalReturn,
      realizedPnl: totalReal, unrealizedPnl: TODAY_UNREALIZED,
      fees: totalFees,
    },
    positions, recentTrades: trades, equityCurve, dailyPerformance,
    botStatus: {
      mode: 'OFF', strategy: null, marketData: 'PENDING',
      orderExecution: 'DISABLED', riskGuard: 'LOCKED',
      lastActivity: null, uptimeSeconds: null, todayTrades: null, errors: null,
    },
    performance: {
      return7d: 1.85, return30d: totalReturn, totalReturn,
      maxDrawdown, winRate: (wins.length / trades.length) * 100,
      profitFactor: loss > 0 ? profit / loss : null,
      averageTrade: totalReal / trades.length,
    },
    today: {
      realizedPnl:   TODAY_REALIZED,
      unrealizedPnl: TODAY_UNREALIZED,
      fees:          TODAY_FEES,
      trades: todayTrades.length,
      wins:   todayTrades.filter(t => t.pnl! > 0).length,
      losses: todayTrades.filter(t => t.pnl! < 0).length,
      exposurePct: (exposure / CURRENT_EQUITY) * 100,
    },
    dailyBaseline,
  }
}

