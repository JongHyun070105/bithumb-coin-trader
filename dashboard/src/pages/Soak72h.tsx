import { useEvidence } from '../context/useEvidence'
import { uniqueArtifacts } from '../evidence/chainEvaluator'
import { MetricCard } from '../components/MetricCard'
import { ModeBanner } from '../components/ModeBanner'
export const Soak72h = () => {
  const { artifacts, chainEvaluation } = useEvidence()
  const byType = uniqueArtifacts(artifacts)
  const contract = byType.get('epoch_contract')?.rawJson
  const actual = byType.get('actual_start_evidence')?.rawJson
  const verifiedStart = chainEvaluation.nodes
    .slice(0, 2)
    .every((n) => n.status === 'PRESENT' && n.upstreamOk)
  const value = (k: string) =>
    typeof contract?.[k] === 'string' || typeof contract?.[k] === 'number'
      ? String(contract[k])
      : 'NOT AVAILABLE'
  return (
    <div className="page-container">
      <ModeBanner />
      <div className="page-header">
        <div>
          <h2>72시간 무인 수집 현황 (72H Unattended Soak Console)</h2>
          <p className="page-subtitle">
            실행 완료와 DQ 판정은 독립적입니다. 현재 실행 상태는 조회하지
            않습니다.
          </p>
        </div>
      </div>
      <section className="section-block">
        <div className="two-col-grid">
          <div className="card-surface">
            <h3>72H: FINAL RESULT PENDING</h3>
            <p>종료·수집 지속 시간 증거는 이 뷰어에서 검증하지 않았습니다.</p>
          </div>
          <div className="card-surface">
            <h3>REAL DQ: NOT RUN</h3>
            <p>
              반입된 메타데이터 검증은 실제 원시 데이터 감사 결과를 대체하지
              않습니다.
            </p>
          </div>
        </div>
      </section>
      <section className="section-block">
        <div className="status-grid">
          <MetricCard
            title="ACTUAL START TIME"
            value={
              verifiedStart
                ? String(actual?.actual_start_time_utc)
                : 'PENDING EVIDENCE'
            }
            subtext="실제 시작 신원 및 계약의 파일 바이트 SHA 대조 필요"
            evidenceSource={verifiedStart ? 'DERIVED' : 'NOT VERIFIED'}
          />
          <MetricCard
            title="DECLARED DURATION"
            value={value('duration_seconds')}
            subtext="계약에 선언된 초 단위 기간; 실제 지속 시간 아님"
            evidenceSource="DECLARED"
          />
          {[
            'collector_epoch',
            'collector_run_id',
            'runtime_software_commit',
            'runtime_fingerprint',
          ].map((k) => (
            <MetricCard
              key={k}
              title={k}
              value={value(k)}
              evidenceSource={contract ? 'DECLARED' : 'NOT AVAILABLE'}
            />
          ))}
        </div>
      </section>
      <section className="section-block">
        <div className="card-surface">
          <h3>고정된 수집 대상 (DECLARED)</h3>
          <p>
            Bithumb 20 × 3 = 60 · Binance 4 × 2 = 8 · Upbit 4 × 2 = 8 · 총 76
            피드
          </p>
          <p>
            실제 코호트 수·지연·재연결·누락 수치는 검증된 측정 증거 없이는
            표시하지 않습니다.
          </p>
        </div>
      </section>
    </div>
  )
}
