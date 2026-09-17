import React from 'react'
import { useEvidence } from '../context/useEvidence'
import { MetricCard } from '../components/MetricCard'
import { PipelineStrip } from '../components/PipelineStrip'
import { EvidenceDropZone } from '../components/EvidenceDropZone'
import { ModeBanner } from '../components/ModeBanner'
import { StatusBadge } from '../components/StatusBadge'
import { GitBranch, Shield, FileCheck } from 'lucide-react'
import { V2_RESEARCH_STATUS, V4_VALIDATION_STATUS } from '../fixtures/syntheticDemoData'

export const Overview: React.FC = () => {
  const { projectSummary, pipelineStages, artifacts, chainEvaluation } = useEvidence()

  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>오프라인 증거 및 연구 콘솔 (Overview v0.3)</h2>
          <p className="page-subtitle">
            Bithumb Coin Trader — V2 연구 완료 / V4 검증 진행 중
          </p>
        </div>
        <div className="repo-status-pill">
          <GitBranch size={14} />
          <span>OFFLINE TOOLING: <strong>{projectSummary.offlineTooling}</strong></span>
          <StatusBadge status={projectSummary.syntheticVerification} label="SYNTHETIC VERIFIED" size="sm" />
        </div>
      </div>

      {/* Current Research State */}
      <section className="section-block">
        <h3 className="section-title">
          <span>현재 연구 상태 (Current Research State)</span>
        </h3>
        <div className="status-grid">
          <MetricCard
            title="PROJECT MODE"
            value={projectSummary.projectMode}
            subtext="격리 오프라인 연구 모드"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="72H SOAK STATUS"
            value={projectSummary.soak72hStatus}
            subtext={projectSummary.soak72hStatus === 'PENDING' ? 'AWS 자율 구동 중 (결과 대기)' : '259,200s 수집 완료 증거 확인'}
            status={projectSummary.soak72hStatus === 'PASS' ? 'success' : 'warning'}
            evidenceSource={projectSummary.soak72hStatus === 'PASS' ? 'MEASURED' : 'NOT AVAILABLE'}
          />
          <MetricCard
            title="REAL DATA DQ"
            value={projectSummary.realDqStatus}
            subtext={projectSummary.realDqStatus === 'NOT RUN' ? '원시 데이터 수신 후 감사 예정' : '심층 타임스탬프 감사 통과'}
            status={projectSummary.realDqStatus === 'PASS' ? 'success' : projectSummary.realDqStatus === 'DEGRADED' ? 'warning' : 'default'}
            evidenceSource={projectSummary.realDqStatus === 'PASS' ? 'MEASURED' : 'NOT VERIFIED'}
          />
          <MetricCard
            title="ALPHA"
            value="UNPROVEN"
            subtext="가설 사전등록 동결 (피처 검정 전)"
            status="default"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="V2 RESULT"
            value={V2_RESEARCH_STATUS.classification}
            subtext={`${V2_RESEARCH_STATUS.sourceObjects} objects, BTC/ETH/XRP full-resolution`}
            status="warning"
            evidenceSource="MEASURED"
          />
          <MetricCard
            title="V4 STATUS"
            value={V4_VALIDATION_STATUS.status}
            subtext={`Started: ${new Date(V4_VALIDATION_STATUS.actualStart).toLocaleDateString()}`}
            status={V4_VALIDATION_STATUS.status === 'RUNNING' ? 'warning' : 'default'}
            evidenceSource="MEASURED"
          />
          <MetricCard
            title="LIVE TRADING"
            value="DISABLED"
            subtext="실거래 주문 계층 영구 비활성"
            status="danger"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="PRIVATE API"
            value="DISABLED"
            subtext="키 입력·거래 연결 기능 없음"
            status="locked"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="PAPER"
            value="NOT STARTED"
            subtext="실시간 모의 트레이딩 미시행"
            status="locked"
            evidenceSource="DECLARED"
          />
        </div>
      </section>

      {/* Pipeline Lifecycle Strip */}
      <section className="section-block">
        <PipelineStrip stages={pipelineStages} />
      </section>

      {/* Two Column Layout: Evidence Quick Status & Local Import Workspace */}
      <div className="two-col-grid">
        <section className="section-block">
          <h3 className="section-title">
            <FileCheck size={18} />
            <span>증거 사슬 빠른 진단 (Evidence Chain Quick Status)</span>
          </h3>

          <div className="card-surface">
            <div className="quick-summary-row">
              <span className="label">사슬 전체 상태:</span>
              <StatusBadge status={chainEvaluation.overallState} />
            </div>
            <p className="summary-desc">{chainEvaluation.summaryMessage}</p>

            <div className="chain-mini-progress">
              {chainEvaluation.nodes.map((n, idx) => (
                <div key={n.id} className={`mini-node node-${n.status.toLowerCase()}`} title={`${idx + 1}. ${n.name} (${n.status})`}>
                  <span className="mini-num">{idx + 1}</span>
                </div>
              ))}
            </div>

            <div className="quick-stats-row">
              <div className="quick-stat">
                <span className="stat-num">{artifacts.length}</span>
                <span className="stat-label">임포트된 아티팩트</span>
              </div>
              <div className="quick-stat">
                <span className="stat-num">{chainEvaluation.nodes.filter((n) => n.status === 'PRESENT').length} / 7</span>
                <span className="stat-label">확보된 사슬 노드</span>
              </div>
              <div className="quick-stat">
                <span className="stat-num">{chainEvaluation.issues.length}</span>
                <span className="stat-label">감지된 결함 / 블로커</span>
              </div>
            </div>
          </div>
        </section>

        <section className="section-block">
          <h3 className="section-title">
            <Shield size={18} />
            <span>로컬 증거 임포트 (Local Evidence Import)</span>
          </h3>
          <EvidenceDropZone />
        </section>
      </div>
    </div>
  )
}
