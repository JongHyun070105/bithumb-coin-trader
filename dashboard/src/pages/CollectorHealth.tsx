import { Activity } from 'lucide-react'
import { StatusBadge } from '../components/StatusBadge'
import { streamMetrics } from '../mockData'

export function CollectorHealth() {
  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">시장 데이터</span><h1>수집기 상태</h1><p>거래소 × stream telemetry입니다. 값은 정적 개발 fixture입니다.</p></div><StatusBadge value="DEGRADED" /></header>
      <div className="notice"><Activity size={16} /><div><strong>V9 epoch 무결성 게이트는 닫혀 있습니다.</strong><span>Queue drop 수는 영구 기록되지 않아 검증 불가로 표시합니다.</span></div></div>
      <section className="panel table-panel"><div className="collector-table" role="table" aria-label="수집기 stream 상태"><div className="collector-row collector-head" role="row"><span>거래소 / Stream</span><span>상태</span><span>최근 이벤트</span><span>p50 / p95</span><span>재연결</span><span>Queue drop</span><span>데이터 속도</span><span>근거</span></div>{streamMetrics.map((metric) => <div className="collector-row" role="row" key={`${metric.exchange}-${metric.stream}`}><div className="stream-name"><strong>{metric.exchange}</strong><span>{metric.stream}</span>{metric.note && <small>{metric.note}</small>}</div><span data-label="상태"><StatusBadge value={metric.status} /></span><span data-label="최근 이벤트">{metric.latestEvent}</span><span data-label="p50 / p95"><strong>{metric.p50}</strong><small>{metric.p95}</small></span><span data-label="재연결">{metric.reconnects}</span><span data-label="Queue drop" className="muted">{metric.queueDrops}</span><span data-label="데이터 속도">{metric.dataRate}</span><span data-label="근거"><StatusBadge value={metric.metricType} subtle /></span></div>)}</div></section>
      <div className="legend"><span>지표 근거</span><StatusBadge value="MEASURED" subtle /><StatusBadge value="ESTIMATED" subtle /><StatusBadge value="NOT VERIFIABLE" subtle /><StatusBadge value="NOT AVAILABLE" subtle /></div>
    </div>
  )
}
