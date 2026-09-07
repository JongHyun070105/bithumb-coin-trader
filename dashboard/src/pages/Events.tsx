import React, { useState } from 'react'
import { useEvidence } from '../context/useEvidence'
import type { EventCategory, Severity } from '../types'
import { ModeBanner } from '../components/ModeBanner'
import { ScrollText, Filter, Clock } from 'lucide-react'

export const Events: React.FC = () => {
  const { events } = useEvidence()
  const [selectedCat, setSelectedCat] = useState<EventCategory | 'ALL'>('ALL')

  const categories: Array<EventCategory | 'ALL'> = [
    'ALL',
    'COLLECTION',
    'ARCHIVE',
    'DQ',
    'EVIDENCE',
    'RESEARCH',
    'SAFETY',
    'SYSTEM'
  ]

  const filtered = selectedCat === 'ALL' ? events : events.filter((e) => e.category === selectedCat)

  const severityBadges: Record<Severity, string> = {
    INFO: 'badge-neutral',
    WARN: 'badge-warning',
    ERROR: 'badge-danger',
    CRITICAL: 'badge-danger'
  }

  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>이벤트 타임라인 (Derived Events Timeline)</h2>
          <p className="page-subtitle">
            임포트된 증거 아티팩트 및 런타임 수집 기록으로부터 로컬 파생된 시간순 이벤트 로그
          </p>
        </div>
      </div>

      {/* Filter Row */}
      <section className="section-block">
        <div className="filter-bar">
          <div className="filter-label">
            <Filter size={15} />
            <span>카테고리 필터:</span>
          </div>
          <div className="filter-buttons">
            {categories.map((cat) => (
              <button
                key={cat}
                className={`filter-btn ${selectedCat === cat ? 'active' : ''}`}
                onClick={() => setSelectedCat(cat)}
              >
                {cat === 'ALL' ? '전체' : cat}
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* Timeline List */}
      <section className="section-block">
        {filtered.length === 0 ? (
          <div className="card-surface empty-timeline">
            <ScrollText size={32} className="muted-text" />
            <p className="muted-text">표시할 파생 이벤트가 없습니다. 증거 아티팩트를 임포트하거나 합성 데모를 로드하십시오.</p>
          </div>
        ) : (
          <div className="timeline-list">
            {filtered.map((ev) => (
              <div key={ev.id} className="timeline-item">
                <div className="timeline-left">
                  <div className="timeline-time">
                    <Clock size={12} />
                    <span>{ev.time}</span>
                  </div>
                  <span className={`status-badge ${severityBadges[ev.severity]} badge-sm`}>
                    {ev.severity}
                  </span>
                  <span className="cat-badge">{ev.category}</span>
                </div>

                <div className="timeline-content card-surface">
                  <div className="timeline-title-row">
                    <strong>{ev.title}</strong>
                    <span className="source-tag">{ev.source}</span>
                  </div>
                  <p className="timeline-detail">{ev.detail}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
