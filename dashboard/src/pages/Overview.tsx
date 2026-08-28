import { Database, HardDrive, RadioTower, ShieldCheck } from 'lucide-react'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { events, streamMetrics } from '../data/mockData'

export function Overview() {
  const exchanges = ['Bithumb', 'Binance', 'Upbit'].map((exchange) => ({
    exchange,
    status: streamMetrics.some((metric) => metric.exchange === exchange && metric.status === 'DEGRADED') ? 'DEGRADED' : 'HEALTHY',
  }))

  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">Operations snapshot</span><h1>Overview</h1><p>Research infrastructure posture and promotion gates.</p></div><StatusBadge value="DEGRADED" /></header>
      <section className="metric-grid" aria-label="System metrics">
        <MetricCard label="System Mode" value="RESEARCH" meta="Execution disabled" tone="blue" />
        <MetricCard label="Collector" value="DEGRADED" meta="1 open market identity finding" tone="amber" />
        <MetricCard label="Strategy" value="NOT VALIDATED" meta="Research only" />
        <MetricCard label="Live Ready" value="FALSE" meta="Promotion gate locked" tone="red" />
        <MetricCard label="Data Health" value="DEGRADED" meta="V9 infrastructure audit" tone="amber" />
        <MetricCard label="Uptime" value="71h 58m" meta="Estimated mock snapshot" />
      </section>
      <section className="two-column">
        <article className="panel"><div className="panel-title"><div><span className="eyebrow">Pipeline</span><h2>Collector status</h2></div><RadioTower size={18} /></div><div className="exchange-list">{exchanges.map((item) => <div className="exchange-row" key={item.exchange}><span>{item.exchange}</span><StatusBadge value={item.status} /></div>)}</div><div className="phase-strip"><div><span>V9</span><strong>INFRASTRUCTURE AUDIT</strong></div><div><span>V9.1</span><strong>STABILIZATION</strong></div></div></article>
        <article className="panel"><div className="panel-title"><div><span className="eyebrow">Capacity</span><h2>Storage</h2></div><HardDrive size={18} /></div><div className="storage-value"><strong>684 GB</strong><span>of 1 TB mock capacity</span></div><div className="progress-track" aria-label="Storage 68.4 percent"><span style={{ width: '68.4%' }} /></div><div className="storage-stats"><span><Database size={14} /> Raw partitions <strong>1,892</strong></span><span>Free <strong>316 GB</strong></span></div></article>
      </section>
      <section className="two-column">
        <article className="panel unavailable-panel"><div className="panel-title"><div><span className="eyebrow">Portfolio &amp; PnL</span><h2>Trading performance</h2></div><ShieldCheck size={18} /></div><div className="unavailable"><strong>NOT AVAILABLE</strong><p>No account or trading backend is connected. Absence is not represented as zero.</p></div></article>
        <article className="panel"><div className="panel-title"><div><span className="eyebrow">Latest activity</span><h2>Recent events</h2></div><a className="text-link" href="#logs">View all</a></div><div className="compact-events">{events.slice(0, 4).map((event) => <div className="compact-event" key={event.id}><time>{event.time}</time><StatusBadge value={event.severity} subtle /><div><strong>{event.title}</strong><span>{event.category}</span></div></div>)}</div></article>
      </section>
    </div>
  )
}
