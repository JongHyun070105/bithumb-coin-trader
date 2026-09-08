import { calculateTodayMetrics } from './model'
import type { TradingDataState } from './model'

export const available = (value: number | null | undefined): value is number => typeof value === 'number' && Number.isFinite(value)
export const number = (value: number | null | undefined, digits = 0) => available(value) ? new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(value) : '—'
export const money = (value: number | null | undefined, signed = false) => available(value) ? `${value < 0 ? '−' : signed && value > 0 ? '+' : ''}₩${number(Math.abs(value))}` : '—'
export const percent = (value: number | null | undefined, signed = true) => available(value) ? `${value < 0 ? '−' : signed && value > 0 ? '+' : ''}${Math.abs(value).toFixed(2)}%` : '—'
export const tone = (value: number | null | undefined) => !available(value) || value === 0 ? '' : value > 0 ? 'positive' : 'negative'
export const dateLabel = (timestamp: string | undefined, withTime = false) => timestamp && Number.isFinite(Date.parse(timestamp)) ? new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Seoul', month: 'short', day: 'numeric', ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}) }).format(new Date(timestamp)) : '—'
export function duration(start: string, end: string | undefined) {
  if (!end) return '—'
  const minutes = Math.floor((Date.parse(end) - Date.parse(start)) / 60000)
  if (!Number.isFinite(minutes) || minutes < 0) return '—'
  return minutes >= 1440 ? `${Math.floor(minutes / 1440)}d ${Math.floor(minutes % 1440 / 60)}h` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/** Never relabel a synthetic payload as authoritative, or infer returns without a daily baseline. */
export function prepareTradingState(data: TradingDataState): TradingDataState {
  if (data.status !== 'REAL_DATA' && data.status !== 'SYNTHETIC_DEMO') return data
  const expected = data.status === 'REAL_DATA' ? 'authoritative' : 'synthetic'
  if (data.snapshot.source.kind !== expected) return { status: 'ERROR', error: 'The data source does not match the selected mode.' }
  if (data.status === 'SYNTHETIC_DEMO') return data
  const daily = calculateTodayMetrics(data.snapshot.portfolio.equity, data.snapshot.dailyBaseline, data.snapshot.timestamp)
  return { ...data, snapshot: { ...data.snapshot, portfolio: { ...data.snapshot.portfolio, ...daily } } }
}
