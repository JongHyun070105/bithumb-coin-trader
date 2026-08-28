import { AlertTriangle, CheckCircle2, CircleHelp, LockKeyhole, XCircle } from 'lucide-react'
import type { Evidence, Severity, Status } from '../types'

type BadgeValue = Status | Evidence | Severity | string

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

  return <span className={`status-badge status-${normalized}${subtle ? ' subtle' : ''}`}><Icon size={12} aria-hidden="true" />{value}</span>
}
