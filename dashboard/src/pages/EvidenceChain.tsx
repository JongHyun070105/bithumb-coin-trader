import React from 'react'
import { useEvidence } from '../context/useEvidence'
import { EvidenceChainDiagram } from '../components/EvidenceChainDiagram'
import { EvidenceArtifactCard } from '../components/EvidenceArtifactCard'
import { EvidenceDropZone } from '../components/EvidenceDropZone'
import { ModeBanner } from '../components/ModeBanner'
import { Files, KeyRound } from 'lucide-react'

export const EvidenceChain: React.FC = () => {
  const { artifacts, chainEvaluation, removeArtifact } = useEvidence()

  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>권위적 증거 사슬 검증 (Evidence Chain Console)</h2>
          <p className="page-subtitle">
            런타임 봉인부터 연구 데이터셋까지의 7단계 암호학적 SHA-256 연결 사슬 및 교차 검증 상태를 확인합니다.
          </p>
        </div>
      </div>

      {/* 7-Stage Visual Evidence Chain */}
      <section className="section-block">
        <EvidenceChainDiagram
          nodes={chainEvaluation.nodes}
          overallState={chainEvaluation.overallState}
          summaryMessage={chainEvaluation.summaryMessage}
          issues={chainEvaluation.issues}
        />
      </section>

      {/* Tripartite Invariant Verification Card */}
      <section className="section-block">
        <h3 className="section-title">
          <KeyRound size={18} />
          <span>삼자 해시 일관성 불변식 (Tripartite Invariant Check)</span>
        </h3>

        <div className="card-surface invariant-card">
          <div className="invariant-equation">
            <code>canonical.source_epoch_manifest_sha256 == DQ.epoch_manifest_sha256 == actual_epoch_manifest_sha256</code>
          </div>
          <p className="invariant-desc">
            캐노니컬 데이터셋이 기반으로 삼은 에포크 루트 매니페스트와 심층 데이터 품질 감사(DQ)가 통과한 에포크 루트 매니페스트, 그리고 실제 원시 파일시스템의 에포크 루트 매니페스트가 정확히 1비트의 오차도 없이 일치해야 합니다.
          </p>
          <div className="invariant-status-row">
            <span className="inv-label">불변식 검증 상태:</span>
            <span className={`status-badge ${chainEvaluation.overallState === 'COMPLETE' ? 'badge-success' : 'badge-neutral'}`}>
              {chainEvaluation.overallState === 'COMPLETE' ? '삼자 일치 검증 통과 (VERIFIED)' : '증거 체인 불완전 / 대기 중'}
            </span>
          </div>
        </div>
      </section>

      {/* Artifact Files Workspace */}
      <section className="section-block">
        <div className="section-header-row">
          <h3 className="section-title">
            <Files size={18} />
            <span>임포트된 증거 아티팩트 목록 ({artifacts.length}개)</span>
          </h3>
        </div>

        {artifacts.length === 0 ? (
          <div className="empty-artifact-state">
            <p className="muted-text">아직 로컬에 로드된 증거 아티팩트 파일이 없습니다. 아래 드롭존을 사용해 파일을 추가하십시오.</p>
          </div>
        ) : (
          <div className="artifact-cards-grid">
            {artifacts.map((art) => (
              <EvidenceArtifactCard key={art.id} artifact={art} onRemove={removeArtifact} />
            ))}
          </div>
        )}

        <div className="sub-dropzone-box">
          <EvidenceDropZone />
        </div>
      </section>
    </div>
  )
}
