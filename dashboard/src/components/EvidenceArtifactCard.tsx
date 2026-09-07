import React from 'react'
import type { ParsedArtifact } from '../types'
import { FileJson, AlertCircle, CheckCircle2, HelpCircle, X, Hash } from 'lucide-react'

interface EvidenceArtifactCardProps {
  artifact: ParsedArtifact
  onRemove?: (id: string) => void
}

export const EvidenceArtifactCard: React.FC<EvidenceArtifactCardProps> = ({ artifact, onRemove }) => {
  const isOk = artifact.parseStatus === 'SUCCESS'
  const isUnknown = artifact.parseStatus === 'UNKNOWN_TYPE'
  const isFailed = artifact.parseStatus === 'PARSE_FAILED'

  const typeLabels: Record<string, string> = {
    runtime_seal: '런타임 씰 (Runtime Seal)',
    launch_provenance: '런칭 출처 (Launch Provenance)',
    actual_start_evidence: '실제 시작 증거 (Actual Start)',
    epoch_contract: '에포크 계약 (Epoch Contract)',
    epoch_manifest: '에포크 루트 (Epoch Manifest)',
    deep_dq_report: '심층 DQ 보고서 (Deep Audit)',
    dq_qualification: 'DQ 적격성 판정 (DQ Qualification)',
    canonical_manifest: '캐노니컬 루트 (Canonical Manifest)',
    dataset_manifest: '데이터셋 매니페스트 (Dataset Manifest)',
    archive_receipt: '아카이브 영수증 (Archive Receipt)',
    fullscan_report: '풀스캔 감사 (Fullscan Report)',
    unknown: 'UNKNOWN ARTIFACT'
  }

  const label = typeLabels[artifact.type] ?? artifact.type

  return (
    <div className={`artifact-card status-${artifact.parseStatus.toLowerCase()}`}>
      <div className="card-top">
        <div className="card-identity">
          <FileJson size={16} className="file-icon" />
          <strong className="file-name" title={artifact.fileName}>{artifact.fileName}</strong>
        </div>
        {onRemove && (
          <button className="icon-btn-remove" onClick={() => onRemove(artifact.id)} title="아티팩트 제거">
            <X size={14} />
          </button>
        )}
      </div>

      <div className="card-meta">
        <span className={`type-tag type-${artifact.type}`}>
          {label}
        </span>
        <span className="size-tag">{(artifact.fileSize / 1024).toFixed(1)} KB</span>
        {artifact.schemaVersion && (
          <span className="schema-tag">v{artifact.schemaVersion}</span>
        )}
      </div>

      <div className="card-hash">
        <Hash size={12} className="hash-icon" />
        <code className="hash-text" title={artifact.calculatedSha256}>
          {artifact.calculatedSha256.slice(0, 16)}...{artifact.calculatedSha256.slice(-8)}
        </code>
      </div>

      <div className="card-footer">
        {isOk && (
          <span className="status-indicator status-ok">
            <CheckCircle2 size={13} />
            <span>파싱 성공 (Parsed)</span>
          </span>
        )}
        {isUnknown && (
          <span className="status-indicator status-warn">
            <HelpCircle size={13} />
            <span>알 수 없는 아티팩트 (UNKNOWN ARTIFACT)</span>
          </span>
        )}
        {isFailed && (
          <span className="status-indicator status-err">
            <AlertCircle size={13} />
            <span>{artifact.errorMessage ?? 'PARSE FAILED'}</span>
          </span>
        )}
      </div>
    </div>
  )
}
