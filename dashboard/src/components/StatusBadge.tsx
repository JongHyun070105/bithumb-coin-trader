import { AlertTriangle, CheckCircle2, CircleHelp, LockKeyhole, XCircle } from 'lucide-react'
import type { Evidence, Severity, Status } from '../types'

type BadgeValue = Status | Evidence | Severity | string

const koreanLabels: Record<string, string> = {
  HEALTHY: '정상',
  DEGRADED: '저하',
  FAIL: '실패',
  UNKNOWN: '알 수 없음',
  LOCKED: '잠김',
  DISABLED: '비활성',
  FALSE: '아니오',
  MEASURED: '실측',
  ESTIMATED: '추정',
  'NOT VERIFIABLE': '검증 불가',
  'NOT AVAILABLE': '제공 안 됨',
  INFO: '정보',
  WARN: '경고',
  ERROR: '오류',
  CRITICAL: '치명적',
}

export function StatusBadge({ value, subtle = false }: { value: BadgeValue; subtle?: boolean }) {
  const normalized = value.toLowerCase().replaceAll(' ', '-')
  const Icon = value === 'HEALTHY' || value === 'INFO' || value === 'MEASURED'
    ? CheckCircle2
    : value === 'DEGRADED' || value === 'WARN' || value === 'ESTIMATED'
      ? AlertTriangle
      : value === 'FAIL' || value === 'ERROR' || value === 'CRITICAL' || value === 'FALSE'
        ? XCircle
        : value === 'LOCKED' || value === 'DISABLED'
          ? LockKeyhole
          : CircleHelp

  return <span className={`status-badge status-${normalized}${subtle ? ' subtle' : ''}`} title={value}><Icon size={12} aria-hidden="true" />{koreanLabels[value] ?? value}</span>
}
