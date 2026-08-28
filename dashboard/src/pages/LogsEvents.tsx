import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { StatusBadge } from '../components/StatusBadge'
import { events } from '../mockData'
import type { EventCategory } from '../types'

const filters: Array<'ALL' | EventCategory> = ['ALL', 'SYSTEM', 'DATA', 'TRADING', 'SIGNAL', 'RISK', 'RESEARCH']
const filterLabels: Record<(typeof filters)[number], string> = { ALL: '전체', SYSTEM: '시스템', DATA: '데이터', TRADING: '트레이딩', SIGNAL: '신호', RISK: '리스크', RESEARCH: '연구' }

export function LogsEvents() {
  const [active, setActive] = useState<(typeof filters)[number]>('ALL')
  const [query, setQuery] = useState('')
  const visible = useMemo(() => events.filter((event) => (active === 'ALL' || event.category === active) && `${event.title} ${event.detail}`.toLowerCase().includes(query.toLowerCase())), [active, query])
  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">운영 기록</span><h1>로그 / 이벤트</h1><p>시스템·데이터·연구·리스크·트레이딩 모의 이벤트 타임라인입니다.</p></div><StatusBadge value="MEASURED" /></header>
      <section className="event-toolbar" aria-label="이벤트 필터"><div className="filter-group">{filters.map((filter) => <button className={active === filter ? 'active' : ''} key={filter} onClick={() => setActive(filter)} type="button">{filterLabels[filter]}</button>)}</div><label className="search-field"><Search size={15} /><span className="sr-only">이벤트 검색</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이벤트 검색" /></label></section>
      <section className="panel timeline-panel"><div className="timeline">{visible.map((event) => <article className={`timeline-event severity-${event.severity.toLowerCase()}`} key={event.id}><div className="event-rail"><span /><time>{event.time}</time></div><div className="event-body"><div className="event-meta"><StatusBadge value={event.severity} /><span>{filterLabels[event.category]}</span><code>{event.id}</code></div><h2>{event.title}</h2><p>{event.detail}</p></div></article>)}{visible.length === 0 && <div className="empty-state">현재 필터와 일치하는 이벤트가 없습니다.</div>}</div></section>
    </div>
  )
}
