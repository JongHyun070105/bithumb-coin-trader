import React from 'react'
import type { ChainNodeState, ChainOverallState } from '../types'
import { CheckCircle2, AlertTriangle, XCircle, Clock, Link2, ShieldAlert } from 'lucide-react'

interface EvidenceChainDiagramProps {
  nodes: ChainNodeState[]
  overallState: ChainOverallState
  summaryMessage: string
  issues: string[]
}

export const EvidenceChainDiagram: React.FC<EvidenceChainDiagramProps> = ({
  nodes,
  overallState,
  summaryMessage,
  issues
}) => {
  const stateBadges: Record<ChainOverallState, { label: string; cls: string }> = {
    COMPLETE: { label: '증거 사슬 완전 봉인 (COMPLETE)', cls: 'badge-success' },
    INCOMPLETE: { label: '증거 사슬 불완전 (INCOMPLETE)', cls: 'badge-warning' },
    MISMATCH: { label: '암호학적 해시 불일치 (MISMATCH)', cls: 'badge-danger' },
    INVALID: { label: '증거 부적격 / 결함 (INVALID)', cls: 'badge-danger' },
    'NOT ENOUGH EVIDENCE': { label: '증거 부족 (NOT ENOUGH EVIDENCE)', cls: 'badge-neutral' }
  }

  const overall = stateBadges[overallState]

  return (
    <div className="chain-diagram-wrapper">
      <div className="chain-diagram-header">
        <div>
          <h3>7단계 권위적 증거 사슬 (Authoritative Evidence Chain)</h3>
          <p className="muted-text">{summaryMessage}</p>
        </div>
        <div>
          <span className={`status-badge ${overall.cls}`}>{overall.label}</span>
        </div>
      </div>

      {issues.length > 0 && (
        <div className="chain-issues-alert">
          <div className="issues-title">
            <ShieldAlert size={16} />
            <strong>증거 사슬 블로커 및 무결성 경고 ({issues.length}건)</strong>
          </div>
          <ul>
            {issues.map((iss, idx) => (
              <li key={idx}>{iss}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="chain-flow-container">
        {nodes.map((node, index) => {
          const isPresent = node.status === 'PRESENT'
          const isMissing = node.status === 'MISSING'
          const isMismatch = node.status === 'MISMATCH'
          const isInvalid = node.status === 'INVALID'

          return (
            <React.Fragment key={node.id}>
              <div className={`chain-node node-${node.status.toLowerCase()} ${node.upstreamOk ? 'upstream-ok' : 'upstream-blocked'}`}>
                <div className="node-step-badge">단계 {index + 1}</div>

                <div className="node-title">
                  <strong>{node.name}</strong>
                </div>

                <div className="node-status-row">
                  {isPresent && (
                    <span className="node-status-chip chip-present">
                      <CheckCircle2 size={12} />
                      PRESENT
                    </span>
                  )}
                  {isMissing && (
                    <span className="node-status-chip chip-missing">
                      <Clock size={12} />
                      MISSING
                    </span>
                  )}
                  {isMismatch && (
                    <span className="node-status-chip chip-mismatch">
                      <XCircle size={12} />
                      MISMATCH
                    </span>
                  )}
                  {isInvalid && (
                    <span className="node-status-chip chip-invalid">
                      <AlertTriangle size={12} />
                      INVALID
                    </span>
                  )}
                </div>

                <div className="node-details">
                  <div className="detail-item">
                    <span className="detail-label">아티팩트:</span>
                    <span className="detail-val">{node.artifact?.fileName ?? '미확인'}</span>
                  </div>
                  {node.claimedSha && (
                    <div className="detail-item">
                      <span className="detail-label">해시:</span>
                      <code className="detail-hash">{node.claimedSha.slice(0, 12)}...</code>
                    </div>
                  )}
                  {node.notes && <div className="node-notes">{node.notes}</div>}
                </div>
              </div>

              {index < nodes.length - 1 && (
                <div className="chain-connector">
                  <svg width="24" height="40" viewBox="0 0 24 40" className="connector-svg">
                    <path
                      d="M 12 0 L 12 30 M 7 25 L 12 32 L 17 25"
                      stroke="currentColor"
                      strokeWidth="2"
                      fill="none"
                      strokeLinecap="round"
                    />
                  </svg>
                  <Link2 size={12} className="connector-icon" />
                </div>
              )}
            </React.Fragment>
          )
        })}
      </div>
    </div>
  )
}
