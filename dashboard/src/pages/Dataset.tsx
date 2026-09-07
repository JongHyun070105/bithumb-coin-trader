import { useEvidence } from '../context/useEvidence'
import { uniqueArtifacts } from '../evidence/chainEvaluator'
import { MetricCard } from '../components/MetricCard'
import { ModeBanner } from '../components/ModeBanner'
export const Dataset = () => {
  const { artifacts } = useEvidence(),
    dataset = uniqueArtifacts(artifacts).get('dataset_manifest')?.rawJson
  const value = (k: string) =>
    typeof dataset?.[k] === 'string' || typeof dataset?.[k] === 'number'
      ? String(dataset[k])
      : 'NOT AVAILABLE'
  return (
    <div className="page-container">
      <ModeBanner />
      <div className="page-header">
        <div>
          <h2>연구 데이터셋 콘솔 (Dataset Console)</h2>
          <p className="page-subtitle">
            반입된 manifest 메타데이터만 표시합니다. 파티션 내용은 열지
            않습니다.
          </p>
        </div>
      </div>
      <section className="section-block">
        <div className="card-surface">
          <h3>HOLDOUT: SEALED</h3>
          <p>
            이 뷰어에는 홀드아웃 열람·해제 기능이 없습니다. 아래 수치는
            manifest에 선언된 값이며 파티션 바이트는 미검증 상태입니다.
          </p>
        </div>
      </section>
      <section className="section-block">
        <div className="status-grid">
          {[
            'dataset_id',
            'source_epoch_id',
            'source_run_id',
            'source_runtime_commit',
            'source_runtime_fingerprint',
            'epoch_manifest_sha256',
            'deep_dq_report_sha256',
            'dq_qualification_sha256',
            'canonical_manifest_sha256',
            'canonicalizer_commit',
            'dataset_builder_commit',
            'source_record_count',
            'train_records',
            'validation_records',
            'holdout_records',
          ].map((k) => (
            <MetricCard
              key={k}
              title={k}
              value={value(k)}
              evidenceSource={dataset ? 'DECLARED' : 'NOT AVAILABLE'}
            />
          ))}
        </div>
      </section>
    </div>
  )
}
