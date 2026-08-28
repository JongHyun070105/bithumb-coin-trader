export type Status = 'HEALTHY' | 'DEGRADED' | 'FAIL' | 'UNKNOWN' | 'LOCKED' | 'DISABLED' | 'FALSE'
export type Evidence = 'MEASURED' | 'ESTIMATED' | 'NOT VERIFIABLE' | 'NOT AVAILABLE'
export type EventCategory = 'SYSTEM' | 'DATA' | 'TRADING' | 'SIGNAL' | 'RISK' | 'RESEARCH'
export type Severity = 'INFO' | 'WARN' | 'ERROR' | 'CRITICAL'

export interface StreamMetric {
  exchange: string
  stream: string
  status: Status
  latestEvent: string
  p50: string
  p95: string
  reconnects: string
  queueDrops: string
  dataRate: string
  metricType: Evidence
  note?: string
}

export interface OpsEvent {
  id: string
  time: string
  category: EventCategory
  severity: Severity
  title: string
  detail: string
}
