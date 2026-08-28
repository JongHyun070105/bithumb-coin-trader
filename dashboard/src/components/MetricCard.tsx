import type { ReactNode } from 'react'

export function MetricCard({ label, value, meta, tone }: { label: string; value: ReactNode; meta?: string; tone?: string }) {
  return (
    <article className={`metric-card${tone ? ` metric-${tone}` : ''}`}>
      <span className="eyebrow">{label}</span>
      <div className="metric-value">{value}</div>
      {meta && <span className="metric-meta">{meta}</span>}
    </article>
  )
}
