import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { StatusBadge } from '../components/StatusBadge'
import { events } from '../data/mockData'
import type { EventCategory } from '../types'

const filters: Array<'ALL' | EventCategory> = ['ALL', 'SYSTEM', 'DATA', 'TRADING', 'SIGNAL', 'RISK', 'RESEARCH']

export function LogsEvents() {
  const [active, setActive] = useState<(typeof filters)[number]>('ALL')
  const [query, setQuery] = useState('')
  const visible = useMemo(() => events.filter((event) => (active === 'ALL' || event.category === active) && `${event.title} ${event.detail}`.toLowerCase().includes(query.toLowerCase())), [active, query])
  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">Operational record</span><h1>Logs / Events</h1><p>Mock timeline for system, data, research, risk, and trading events.</p></div><StatusBadge value="MEASURED" /></header>
      <section className="event-toolbar" aria-label="Event filters"><div className="filter-group">{filters.map((filter) => <button className={active === filter ? 'active' : ''} key={filter} onClick={() => setActive(filter)} type="button">{filter}</button>)}</div><label className="search-field"><Search size={15} /><span className="sr-only">Search events</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search events" /></label></section>
      <section className="panel timeline-panel"><div className="timeline">{visible.map((event) => <article className={`timeline-event severity-${event.severity.toLowerCase()}`} key={event.id}><div className="event-rail"><span /><time>{event.time}</time></div><div className="event-body"><div className="event-meta"><StatusBadge value={event.severity} /><span>{event.category}</span><code>{event.id}</code></div><h2>{event.title}</h2><p>{event.detail}</p></div></article>)}{visible.length === 0 && <div className="empty-state">No events match the current filter.</div>}</div></section>
    </div>
  )
}
