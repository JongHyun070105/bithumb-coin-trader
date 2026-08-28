import { LockKeyhole, ShieldAlert } from 'lucide-react'
import { StatusBadge } from '../components/StatusBadge'
import { safetyChecks } from '../mockData'

export function SafetyCenter() {
  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">읽기 전용 제어</span><h1>안전 센터</h1><p>데이터·연구·모의투자·실거래 승격을 막는 하드 게이트입니다.</p></div><StatusBadge value="LOCKED" /></header>
      <section className="safety-banner"><div className="safety-icon"><ShieldAlert size={26} /></div><div><span className="eyebrow">전체 안전 상태</span><h2>실거래 비활성</h2><p>이 화면에서는 신규 진입, 계좌 연결, 주문 제출을 활성화할 수 없습니다.</p></div><LockKeyhole size={26} /></section>
      <section className="safety-grid" aria-label="Safety checks">{safetyChecks.map((check) => <article className="safety-card" key={check.label}><div className="safety-card-top"><span>{check.label}</span><StatusBadge value={check.status} /></div><strong>{check.value}</strong><p>{check.detail}</p></article>)}</section>
      <p className="read-only-note"><LockKeyhole size={14} /> 읽기 전용 화면입니다. 안전 설정을 우회하는 제어는 구현하지 않았습니다.</p>
    </div>
  )
}
