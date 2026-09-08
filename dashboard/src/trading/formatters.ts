/**
 * Trading Data Formatters
 *
 * Centralized formatting for KRW money, percentages, durations,
 * KST timestamps, and asset/pair symbols.
 * Null/undefined/NaN is always formatted as '—' (em-dash), never substituted with zero.
 */

export const isAvailable = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value)

/**
 * Format general number with thousands separator.
 */
export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (!isAvailable(value)) return '—'
  return new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(value)
}

/**
 * Format KRW money value.
 * e.g., 10843200 -> "₩10,843,200", -7600 -> "−₩7,600", +91000 (signed) -> "+₩91,000"
 */
export function formatMoney(value: number | null | undefined, signed = false): string {
  if (!isAvailable(value)) return '—'
  const sign = value < 0 ? '−' : signed && value > 0 ? '+' : ''
  const absFormatted = formatNumber(Math.abs(value), 0)
  return `${sign}₩${absFormatted}`
}

/**
 * Format percentage.
 * e.g., 1.28 -> "+1.28%" (signed) or "1.28%" (unsigned), -0.45 -> "−0.45%"
 */
export function formatPercent(value: number | null | undefined, signed = true, digits = 2): string {
  if (!isAvailable(value)) return '—'
  const sign = value < 0 ? '−' : signed && value > 0 ? '+' : ''
  return `${sign}${Math.abs(value).toFixed(digits)}%`
}

/**
 * Return CSS tone class name based on numerical value sign.
 */
export function formatTone(value: number | null | undefined): 'positive' | 'negative' | '' {
  if (!isAvailable(value) || value === 0) return ''
  return value > 0 ? 'positive' : 'negative'
}

/**
 * Format timestamp into Asia/Seoul (KST) date/time string.
 */
export function formatKstDate(timestamp: string | null | undefined, withTime = false): string {
  if (!timestamp) return '—'
  const parsed = Date.parse(timestamp)
  if (!Number.isFinite(parsed)) return '—'

  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'short',
    day: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
  }).format(new Date(parsed))
}

/**
 * Format duration between two ISO timestamps into days, hours, and minutes.
 */
export function formatDuration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return '—'
  const startTime = Date.parse(start)
  const endTime = Date.parse(end)
  if (!Number.isFinite(startTime) || !Number.isFinite(endTime)) return '—'

  const minutes = Math.floor((endTime - startTime) / 60000)
  if (!Number.isFinite(minutes) || minutes < 0) return '—'

  if (minutes >= 1440) {
    const days = Math.floor(minutes / 1440)
    const hours = Math.floor((minutes % 1440) / 60)
    return `${days}일 ${hours}시간`
  }
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return `${hours}시간 ${mins}분`
}

/**
 * Format trading pair or asset name cleanly.
 */
export function formatPair(asset: string, quote = 'KRW'): string {
  if (!asset) return '—'
  const upper = asset.toUpperCase()
  if (upper.includes('/') || upper.includes('_') || upper.includes('-')) {
    return upper.replace(/[_]/g, '/')
  }
  return `${upper}/${quote.toUpperCase()}`
}

/**
 * Re-export backward compatible helpers
 */
export const available = isAvailable
export const number = formatNumber
export const money = formatMoney
export const percent = formatPercent
export const tone = formatTone
export const dateLabel = formatKstDate
export const duration = formatDuration
