import React from 'react'
import type { Status } from '../types'

interface StatusBadgeProps {
  status: Status | string
  label?: string
  size?: 'sm' | 'md'
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({ status, label, size = 'md' }) => {
  const normalized = status.toUpperCase()
  const display = label ?? normalized

  let colorClass = 'badge-unknown'
  if (['HEALTHY', 'PASS', 'COMPLETE'].includes(normalized)) {
    colorClass = 'badge-success'
  } else if (['DEGRADED', 'WARN', 'PENDING', 'RUNNING'].includes(normalized)) {
    colorClass = 'badge-warning'
  } else if (['FAIL', 'FAILED', 'BLOCKED', 'CRITICAL', 'ERROR', 'MISMATCH', 'INVALID'].includes(normalized)) {
    colorClass = 'badge-danger'
  } else if (['LOCKED', 'DISABLED', 'FALSE', 'SEALED'].includes(normalized)) {
    colorClass = 'badge-locked'
  } else if (['UNPROVEN', 'NOT_STARTED', 'NOT_RUN', 'NOT LOADED'].includes(normalized)) {
    colorClass = 'badge-neutral'
  }

  return (
    <span className={`status-badge ${colorClass} badge-${size}`}>
      <span className="status-dot" />
      {display}
    </span>
  )
}
