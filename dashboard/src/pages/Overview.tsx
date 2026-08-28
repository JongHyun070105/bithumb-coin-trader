import { Database, HardDrive, RadioTower, ShieldCheck } from 'lucide-react'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { events, streamMetrics } from '../mockData'

const categoryLabels = { SYSTEM: '시스템', DATA: '데이터', TRADING: '트레이딩', SIGNAL: '신호', RISK: '리스크', RESEARCH: '연구' }

export function Overview() {
  const exchanges = ['빗썸', '바이낸스', '업비트'].map((exchange) => ({
    exchange,
    status: streamMetrics.some((metric) => metric.exchange === exchange && metric.status === 'DEGRADED') ? 'DEGRADED' : 'HEALTHY',
  }))

  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">운영 스냅샷</span><h1>개요</h1><p>연구 인프라 상태와 승격 게이트를 보여줍니다.</p></div><StatusBadge value="DEGRADED" /></header>
      <section className="metric-grid" aria-label="시스템 지표">
        <MetricCard label="시스템 모드" value="연구" meta="실행 비활성" tone="blue" />
        <MetricCard label="수집기" value="저하" meta="시장 식별 결함 1건 열림" tone="amber" />
        <MetricCard label="전략" value="검증 안 됨" meta="연구 전용" />
        <MetricCard label="실거래 준비" value="아니오" meta="승격 게이트 잠김" tone="red" />
        <MetricCard label="데이터 상태" value="저하" meta="V9 인프라 감사" tone="amber" />
        <MetricCard label="가동 시간" value="71시간 58분" meta="추정 모의 스냅샷" />
      </section>
      <section className="two-column">
        <article className="panel"><div className="panel-title"><div><span className="eyebrow">파이프라인</span><h2>수집기 상태</h2></div><RadioTower size={18} /></div><div className="exchange-list">{exchanges.map((item) => <div className="exchange-row" key={item.exchange}><span>{item.exchange}</span><StatusBadge value={item.status} /></div>)}</div><div className="phase-strip"><div><span>V9</span><strong>인프라 감사</strong></div><div><span>V9.1</span><strong>안정화</strong></div></div></article>
        <article className="panel"><div className="panel-title"><div><span className="eyebrow">용량</span><h2>저장공간</h2></div><HardDrive size={18} /></div><div className="storage-value"><strong>684 GB</strong><span>모의 용량 1 TB 중</span></div><div className="progress-track" aria-label="저장공간 68.4퍼센트"><span style={{ width: '68.4%' }} /></div><div className="storage-stats"><span><Database size={14} /> Raw 파티션 <strong>1,892</strong></span><span>여유 <strong>316 GB</strong></span></div></article>
      </section>
      <section className="two-column">
        <article className="panel unavailable-panel"><div className="panel-title"><div><span className="eyebrow">포트폴리오 &amp; 손익</span><h2>트레이딩 성과</h2></div><ShieldCheck size={18} /></div><div className="unavailable"><strong>제공 안 됨</strong><p>계좌나 트레이딩 backend가 연결되지 않았습니다. 없는 값을 0으로 표시하지 않습니다.</p></div></article>
        <article className="panel"><div className="panel-title"><div><span className="eyebrow">최근 활동</span><h2>최근 이벤트</h2></div><a className="text-link" href="#logs">전체 보기</a></div><div className="compact-events">{events.slice(0, 4).map((event) => <div className="compact-event" key={event.id}><time>{event.time}</time><StatusBadge value={event.severity} subtle /><div><strong>{event.title}</strong><span>{categoryLabels[event.category]}</span></div></div>)}</div></article>
      </section>
    </div>
  )
}
