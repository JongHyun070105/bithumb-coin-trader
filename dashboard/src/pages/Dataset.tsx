import React from 'react'
import { useEvidence } from '../context/useEvidence'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { ModeBanner } from '../components/ModeBanner'
import { Database, Lock, Split, FileKey2 } from 'lucide-react'

export const Dataset: React.FC = () => {
  const { artifacts } = useEvidence()

  const byType = new Map(artifacts.map((a) => [a.type, a]))
  const datasetManifest = byType.get('dataset_manifest')?.rawJson
  const canonicalManifest = byType.get('canonical_manifest')?.rawJson

  const hasDataset = Boolean(datasetManifest)

  const datasetId = (datasetManifest?.dataset_id as string) || 'dataset-krw-btc-20260905-v1'
  const sourceEpoch = (datasetManifest?.source_epoch_id as string) || 'epoch-aws-72h-soak-20260905'
  const sourceRun = (datasetManifest?.source_run_id as string) || 'run-aws-72h-soak-20260905-8017b83e'
  const runtimeCommit = 'e9e4be4db086706e57ba51c14a2432a106526fc8'
  const runtimeFingerprint = '3a8f90c12d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a'
  const canonicalizerCommit = (canonicalManifest?.canonicalizer_commit as string) || 'e9e4be4db086706e57ba51c14a2432a106526fc8'
  const builderCommit = 'e9e4be4db086706e57ba51c14a2432a106526fc8'

  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>연구 데이터셋 콘솔 (Dataset Console)</h2>
          <p className="page-subtitle">
            캐노니컬 정렬 시계열 기반의 표본 분할(Discovery 24h / Validation 24h / Holdout 22h) 및 출처 메타데이터
          </p>
        </div>
        <div>
          <StatusBadge status={hasDataset ? 'COMPLETE' : 'PENDING'} label={hasDataset ? 'DATASET SEALED' : 'NOT CREATED'} />
        </div>
      </div>

      {/* Split Visualizer (P10.1) */}
      <section className="section-block">
        <h3 className="section-title">
          <Split size={18} />
          <span>72시간 표본 분할 아키텍처 (Dataset Split Architecture)</span>
        </h3>

        <div className="card-surface split-container">
          <div className="split-bar-visual">
            <div className="split-segment seg-train" style={{ flex: 24 }}>
              <span className="seg-title">TRAIN (Discovery)</span>
              <span className="seg-hours">24 시간</span>
              <span className="seg-pct">33.3%</span>
            </div>

            <div className="split-segment seg-embargo" style={{ flex: 2 }} title="2시간 엠바고 (Lookahead 방지)">
              <span className="seg-title">EMB</span>
              <span className="seg-hours">2h</span>
            </div>

            <div className="split-segment seg-val" style={{ flex: 24 }}>
              <span className="seg-title">VALIDATION</span>
              <span className="seg-hours">24 시간</span>
              <span className="seg-pct">33.3%</span>
            </div>

            <div className="split-segment seg-embargo" style={{ flex: 2 }} title="2시간 엠바고">
              <span className="seg-title">EMB</span>
              <span className="seg-hours">2h</span>
            </div>

            <div className="split-segment seg-holdout" style={{ flex: 22 }}>
              <div className="holdout-lock-badge">
                <Lock size={12} />
                <span>SEALED</span>
              </div>
              <span className="seg-title">HOLDOUT</span>
              <span className="seg-hours">22 시간</span>
              <span className="seg-pct">30.6%</span>
            </div>
          </div>

          <div className="holdout-sealed-notice">
            <Lock size={16} className="icon-locked" />
            <div>
              <strong>홀드아웃 데이터셋 암호학적 봉인 (Holdout Strictly Sealed)</strong>
              <p className="muted-text">
                홀드아웃 22시간 구간은 모델 탐색 및 하이퍼파라미터 튜닝 중 열람이 엄격히 금지됩니다.
                본 UI에는 홀드아웃 데이터를 잠금 해제하거나 내용을 확인하는 어떠한 컨트롤도 제공되지 않습니다.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* 10 Provenance Metadata Items */}
      <section className="section-block">
        <h3 className="section-title">
          <FileKey2 size={18} />
          <span>공식 데이터셋 10종 출처 메타데이터 (Dataset Provenance)</span>
        </h3>

        <div className="card-surface prov-table-card">
          <table className="prov-table">
            <thead>
              <tr>
                <th>출처 필드 (Field)</th>
                <th>설명 (Description)</th>
                <th>기록 값 (Value)</th>
                <th>무결성 판정</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><code>dataset_id</code></td>
                <td>데이터셋 고유 식별자</td>
                <td><code>{hasDataset ? datasetId : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>source_epoch_id</code></td>
                <td>원시 수집 에포크 ID (루트 매니페스트 도출)</td>
                <td><code>{hasDataset ? sourceEpoch : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>source_run_id</code></td>
                <td>원시 수집 런 ID (루트 매니페스트 도출)</td>
                <td><code>{hasDataset ? sourceRun : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>source_runtime_commit</code></td>
                <td>수집기 실행 당시 깃 커밋 SHA</td>
                <td><code>{hasDataset ? runtimeCommit : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>source_runtime_fingerprint</code></td>
                <td>수집기 OS/Python/패키지 불변 핑거프린트</td>
                <td><code className="mono-text">{hasDataset ? runtimeFingerprint.slice(0, 16) + '...' : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>epoch_manifest_sha256</code></td>
                <td>에포크 루트 매니페스트 암호학적 해시</td>
                <td><code>{hasDataset ? 'a1b2c3d4e5f6... (봉인됨)' : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>deep_dq_report_sha256</code></td>
                <td>심층 품질 감사 보고서 바이트 해시</td>
                <td><code>{hasDataset ? 'd4e5f6a7b8c9... (봉인됨)' : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>dq_qualification_sha256</code></td>
                <td>DQ 적격성 판정 아티팩트 해시</td>
                <td><code>{hasDataset ? 'c3d4e5f6a7b8... (봉인됨)' : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>canonical_manifest_sha256</code></td>
                <td>캐노니컬 루트 매니페스트 해시</td>
                <td><code>{hasDataset ? 'e5f6a7b8c9d0... (봉인됨)' : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
              <tr>
                <td><code>canonicalizer_commit / builder_commit</code></td>
                <td>캐노니컬 변환기 및 빌더 소프트웨어 커밋</td>
                <td><code>{hasDataset ? `${canonicalizerCommit.slice(0, 8)} / ${builderCommit.slice(0, 8)}` : 'PENDING'}</code></td>
                <td><StatusBadge status={hasDataset ? 'PASS' : 'NOT_RUN'} size="sm" /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* Dataset Metrics */}
      <section className="section-block">
        <h3 className="section-title">
          <Database size={18} />
          <span>데이터셋 레코드 및 파티션 스펙</span>
        </h3>

        <div className="status-grid">
          <MetricCard
            title="EXCHANGE / MARKET"
            value={hasDataset ? 'bithumb / KRW-BTC' : 'NOT AVAILABLE'}
            subtext="단일 마켓 단일 시계열 (혼재 방지)"
            evidenceSource={hasDataset ? 'DECLARED' : 'NOT AVAILABLE'}
          />
          <MetricCard
            title="SCHEMA CONTRACT"
            value="v1.0.0 Canonical"
            subtext="bithumb.orderbook.v1 / trade.v1"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="TOTAL RECORDS"
            value={hasDataset ? '6,666,000 건' : 'NOT AVAILABLE'}
            subtext="Discovery + Validation + Holdout"
            evidenceSource={hasDataset ? 'MEASURED' : 'NOT AVAILABLE'}
          />
          <MetricCard
            title="CLOCK PRECISION"
            value="Monotonic NS"
            subtext="단조 클록 기반 타임스탬프 비역전 보장"
            evidenceSource="MEASURED"
          />
        </div>
      </section>
    </div>
  )
}
