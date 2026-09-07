import { useEvidence } from '../context/useEvidence'
import { uniqueArtifacts } from '../evidence/chainEvaluator'
import { MetricCard } from '../components/MetricCard'
import { ModeBanner } from '../components/ModeBanner'
export const DataQuality = () => {
  const { artifacts, chainEvaluation } = useEvidence()
  const byType = uniqueArtifacts(artifacts),
    report = byType.get('deep_dq_report')?.rawJson,
    qual = byType.get('dq_qualification')?.rawJson
  const qualified = chainEvaluation.nodes
    .slice(0, 5)
    .every((n) => n.status === 'PRESENT' && n.upstreamOk)
  const metric = (k: string) =>
    typeof qual?.[k] === 'number' ? String(qual[k]) : 'NOT AVAILABLE'
  return (
    <div className="page-container">
      <ModeBanner />
      <div className="page-header">
        <div>
          <h2>심층 데이터 품질 감사 (Deep DQ Audit Console)</h2>
          <p className="page-subtitle">
            REAL DQ: NOT RUN · 아래는 반입된 보고서 메타데이터의 검증
            결과입니다.
          </p>
        </div>
      </div>
      <section className="section-block">
        <div className="card-surface">
          <h3>
            {qualified
              ? 'DQ_PASS — 보고서 참조 및 자체 해시 대조 완료'
              : 'NOT VERIFIED / INCOMPLETE'}
          </h3>
          <p>
            원시 레코드와 외부 진위는 재검증하지 않았습니다. 72H 완료 판정과
            독립적입니다.
          </p>
        </div>
      </section>
      <section className="section-block">
        <div className="status-grid">
          {['hard_fail_count', 'unknown_count', 'degraded_count'].map((k) => (
            <MetricCard
              key={k}
              title={k}
              value={metric(k)}
              evidenceSource={qual ? 'DECLARED' : 'NOT AVAILABLE'}
            />
          ))}
          <MetricCard
            title="AUDIT STATUS"
            value={
              typeof report?.status === 'string'
                ? report.status
                : 'NOT AVAILABLE'
            }
            evidenceSource={report ? 'DECLARED' : 'NOT AVAILABLE'}
          />
        </div>
      </section>
      <section className="section-block">
        <div className="two-col-grid">
          {['blockers', 'warnings'].map((k) => (
            <div className="card-surface" key={k}>
              <h3>{k}</h3>
              {Array.isArray(report?.[k]) ? (
                <>
                  <p>{(report[k] as unknown[]).length} 건 (보고서 선언)</p>
                  <ul>
                    {(report[k] as unknown[]).map((x, i) => (
                      <li key={i}>{String(x)}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>NOT AVAILABLE</p>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
