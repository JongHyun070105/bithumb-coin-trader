import { describe, expect, it } from 'vitest'
import {
  formatDuration,
  formatKstDate,
  formatMoney,
  formatNumber,
  formatPair,
  formatPercent,
  formatTone,
  isAvailable,
} from './formatters'

describe('formatters', () => {
  describe('isAvailable', () => {
    it('returns true only for finite numbers', () => {
      expect(isAvailable(0)).toBe(true)
      expect(isAvailable(100)).toBe(true)
      expect(isAvailable(-50)).toBe(true)
      expect(isAvailable(null)).toBe(false)
      expect(isAvailable(undefined)).toBe(false)
      expect(isAvailable(NaN)).toBe(false)
      expect(isAvailable(Infinity)).toBe(false)
    })
  })

  describe('formatNumber', () => {
    it('formats thousands separators', () => {
      expect(formatNumber(1234567)).toBe('1,234,567')
      expect(formatNumber(0)).toBe('0')
      expect(formatNumber(-9876)).toBe('-9,876')
      expect(formatNumber(12.3456, 2)).toBe('12.35')
      expect(formatNumber(null)).toBe('—')
      expect(formatNumber(undefined)).toBe('—')
    })
  })

  describe('formatMoney', () => {
    it('formats KRW currency with proper signs and em-dash for null', () => {
      expect(formatMoney(10843200)).toBe('₩10,843,200')
      expect(formatMoney(10843200, true)).toBe('+₩10,843,200')
      expect(formatMoney(0)).toBe('₩0')
      expect(formatMoney(0, true)).toBe('₩0')
      expect(formatMoney(-7600)).toBe('−₩7,600')
      expect(formatMoney(-7600, true)).toBe('−₩7,600')
      expect(formatMoney(null)).toBe('—')
      expect(formatMoney(undefined)).toBe('—')
    })

    it('formats very large KRW values correctly', () => {
      expect(formatMoney(12345678900)).toBe('₩12,345,678,900')
      expect(formatMoney(-98765432100)).toBe('−₩98,765,432,100')
    })
  })

  describe('formatPercent', () => {
    it('formats signed and unsigned percentages with 2-digit rounding', () => {
      expect(formatPercent(1.284)).toBe('+1.28%')
      expect(formatPercent(1.286)).toBe('+1.29%')
      expect(formatPercent(1.28, false)).toBe('1.28%')
      expect(formatPercent(-0.456)).toBe('−0.46%')
      expect(formatPercent(-0.456, false)).toBe('−0.46%')
      expect(formatPercent(0)).toBe('0.00%')
      expect(formatPercent(0, true)).toBe('0.00%')
      expect(formatPercent(null)).toBe('—')
      expect(formatPercent(undefined)).toBe('—')
    })
  })

  describe('formatTone', () => {
    it('returns positive, negative, or empty tone', () => {
      expect(formatTone(100)).toBe('positive')
      expect(formatTone(-50)).toBe('negative')
      expect(formatTone(0)).toBe('')
      expect(formatTone(null)).toBe('')
      expect(formatTone(undefined)).toBe('')
    })
  })

  describe('formatKstDate', () => {
    it('formats KST timestamp correctly', () => {
      const utc = '2026-09-08T05:30:00.000Z' // 14:30 KST
      const formatted = formatKstDate(utc, true)
      expect(formatted).toContain('9월')
      expect(formatted).toContain('8일')
      expect(formatted).toContain('14:30')
      expect(formatKstDate(null)).toBe('—')
      expect(formatKstDate('invalid-date')).toBe('—')
    })
  })

  describe('formatDuration', () => {
    it('calculates duration in days, hours, and minutes', () => {
      const start = '2026-09-08T00:00:00.000Z'
      const end1 = '2026-09-08T02:15:00.000Z'
      const end2 = '2026-09-10T05:30:00.000Z'

      expect(formatDuration(start, end1)).toBe('2시간 15분')
      expect(formatDuration(start, end2)).toBe('2일 5시간')
      expect(formatDuration(start, undefined)).toBe('—')
      expect(formatDuration(undefined, end1)).toBe('—')
      expect(formatDuration(end1, start)).toBe('—') // reversed time returns em-dash
    })
  })

  describe('formatPair', () => {
    it('formats asset to trading pair', () => {
      expect(formatPair('BTC')).toBe('BTC/KRW')
      expect(formatPair('eth')).toBe('ETH/KRW')
      expect(formatPair('BTC/KRW')).toBe('BTC/KRW')
      expect(formatPair('BTC_KRW')).toBe('BTC/KRW')
      expect(formatPair('')).toBe('—')
    })
  })
})
