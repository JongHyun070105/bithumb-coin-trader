import React from 'react'
import type { EvidenceSourceLabel } from '../types'

interface MetricCardProps {
  title: string
  value: React.ReactNode
  subtext?: string
  evidenceSource?: EvidenceSourceLabel
  status?: 'default' | 'success' | 'warning' | 'danger' | 'locked'
  className?: string
}

export const MetricCard: React.FC<MetricCardProps> = ({
  title,
  value,
  subtext,
  evidenceSource,
  status = 'default',
  className = ''
}) => {
  return (
    <article className={`metric-card card-status-${status} ${className}`}>
      <div className="metric-header">
        <span className="metric-title">{title}</span>
        {evidenceSource && (
          <span className={`evidence-source-tag source-${evidenceSource.toLowerCase().replace(/\s+/g, '-')}`}>
            {evidenceSource}
          </span>
        )}
      </div>
      <div className="metric-value">{value}</div>
      {subtext && <div className="metric-subtext">{subtext}</div>}
    </article>
  )
}
