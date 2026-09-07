import React from 'react'
import type { PipelineStage } from '../types'
import { StatusBadge } from './StatusBadge'

interface PipelineStripProps {
  stages: PipelineStage[]
}

export const PipelineStrip: React.FC<PipelineStripProps> = ({ stages }) => {
  return (
    <div className="pipeline-strip-container">
      <div className="pipeline-strip-header">
        <strong>프로젝트 라이프사이클 파이프라인 (Lifecycle Pipeline)</strong>
        <small className="muted-text">하위 단계의 완료는 상위 단계의 엄격한 암호학적 통과 전까지 자동 추론되지 않습니다.</small>
      </div>
      <div className="pipeline-strip">
        {stages.map((st, idx) => (
          <React.Fragment key={st.id}>
            <div className={`pipeline-step step-${st.status.toLowerCase()}`}>
              <div className="step-top">
                <span className="step-idx">{idx + 1}</span>
                <span className="step-label">{st.label.replace(/^\d+\.\s*/, '')}</span>
              </div>
              <div className="step-status">
                <StatusBadge status={st.status} size="sm" />
              </div>
              <div className="step-detail">{st.detail}</div>
            </div>
            {idx < stages.length - 1 && <div className="pipeline-arrow">→</div>}
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}
