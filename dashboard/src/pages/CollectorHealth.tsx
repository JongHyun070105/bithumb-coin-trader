import { Activity } from 'lucide-react'
import { StatusBadge } from '../components/StatusBadge'
import { streamMetrics } from '../data/mockData'

export function CollectorHealth() {
  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">Market data</span><h1>Collector Health</h1><p>Exchange × stream telemetry. Values are static development fixtures.</p></div><StatusBadge value="DEGRADED" /></header>
      <div className="notice"><Activity size={16} /><div><strong>V9 epoch integrity gate remains closed.</strong><span>Queue-drop counts are not durably recorded and are shown as NOT VERIFIABLE.</span></div></div>
      <section className="panel table-panel"><div className="collector-table" role="table" aria-label="Collector stream health"><div className="collector-row collector-head" role="row"><span>Exchange / Stream</span><span>Status</span><span>Latest</span><span>p50 / p95</span><span>Reconnect</span><span>Queue drops</span><span>Data rate</span><span>Evidence</span></div>{streamMetrics.map((metric) => <div className="collector-row" role="row" key={`${metric.exchange}-${metric.stream}`}><div className="stream-name"><strong>{metric.exchange}</strong><span>{metric.stream}</span>{metric.note && <small>{metric.note}</small>}</div><span data-label="Status"><StatusBadge value={metric.status} /></span><span data-label="Latest">{metric.latestEvent}</span><span data-label="p50 / p95"><strong>{metric.p50}</strong><small>{metric.p95}</small></span><span data-label="Reconnect">{metric.reconnects}</span><span data-label="Queue drops" className="muted">{metric.queueDrops}</span><span data-label="Data rate">{metric.dataRate}</span><span data-label="Evidence"><StatusBadge value={metric.metricType} subtle /></span></div>)}</div></section>
      <div className="legend"><span>Metric provenance</span><StatusBadge value="MEASURED" subtle /><StatusBadge value="ESTIMATED" subtle /><StatusBadge value="NOT VERIFIABLE" subtle /><StatusBadge value="NOT AVAILABLE" subtle /></div>
    </div>
  )
}
