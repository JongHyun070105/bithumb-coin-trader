/**
 * Dashboard Formatters
 *
 * Centralized formatting for KRW money, percentages, durations,
 * timestamps, and research metrics.
 * Null/undefined/NaN formatted as '—' (em-dash), never zero.
 */

export const isAvailable = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value)

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (!isAvailable(value)) return '—'
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(value)
}

export function formatMoney(value: number | null | undefined, signed = false): string {
  if (!isAvailable(value)) return '—'
  const sign = value < 0 ? '−' : signed && value > 0 ? '+' : ''
  const absFormatted = formatNumber(Math.abs(value), 0)
  return `${sign}₩${absFormatted}`
}

export function formatPercent(value: number | null | undefined, signed = true, digits = 2): string {
  if (!isAvailable(value)) return '—'
  const sign = value < 0 ? '−' : signed && value > 0 ? '+' : ''
  return `${sign}${Math.abs(value).toFixed(digits)}%`
}

export function formatBps(value: number | null | undefined, signed = true): string {
  if (!isAvailable(value)) return '—'
  const sign = value < 0 ? '−' : signed && value > 0 ? '+' : ''
  return `${sign}${Math.abs(value).toFixed(2)} bps`
}

export function formatTone(value: number | null | undefined): 'positive' | 'negative' | '' {
  if (!isAvailable(value) || value === 0) return ''
  return value > 0 ? 'positive' : 'negative'
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!isAvailable(seconds)) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

export function formatTimestamp(isoString: string | null | undefined): string {
  if (!isoString) return '—'
  try {
    return new Date(isoString).toLocaleString()
  } catch {
    return '—'
  }
}
