export type DashboardMode = 'NO_EVIDENCE' | 'SYNTHETIC_DEMO' | 'IMPORTED_EVIDENCE'

export type Status =
  | 'HEALTHY'
  | 'PASS'
  | 'DEGRADED'
  | 'FAIL'
  | 'UNKNOWN'
  | 'LOCKED'
  | 'DISABLED'
  | 'PENDING'
  | 'NOT_RUN'
  | 'UNPROVEN'
  | 'COMPLETE'
  | 'RUNNING'
  | 'BLOCKED'
  | 'NOT_STARTED'

export type EvidenceSourceLabel =
  | 'MEASURED'
  | 'DERIVED'
  | 'DECLARED'
  | 'SYNTHETIC'
  | 'UNKNOWN'
  | 'NOT AVAILABLE'
  | 'NOT VERIFIED'
  | 'SEALED'

export type ScopeBadgeType = 'FULL' | 'SAMPLED' | 'MANIFEST-DERIVED' | 'NOT AVAILABLE'

export type ArtifactType =
  | 'runtime_seal'
  | 'launch_provenance'
  | 'actual_start_evidence'
  | 'epoch_contract'
  | 'epoch_manifest'
  | 'deep_dq_report'
  | 'dq_qualification'
  | 'canonical_manifest'
  | 'dataset_manifest'
  | 'archive_receipt'
  | 'fullscan_report'
  | 'unknown'

export type ChainOverallState =
  | 'AMBIGUOUS_EVIDENCE'
  | 'STRUCTURALLY COMPLETE'
  | 'COMPLETE'
  | 'INCOMPLETE'
  | 'MISMATCH'
  | 'INVALID'
  | 'NOT ENOUGH EVIDENCE'

export type PipelineStageId =
  | 'infra'
  | 'collection'
  | 'seal'
  | 'deep_dq'
  | 'canonical'
  | 'dataset'
  | 'discovery'
  | 'validation'
  | 'holdout'
  | 'paper'
  | 'live'

export interface PipelineStage {
  id: PipelineStageId
  label: string
  status: 'COMPLETE' | 'RUNNING' | 'PENDING' | 'BLOCKED' | 'FAILED' | 'NOT_STARTED'
  detail: string
}

export interface ParsedArtifact {
  id: string
  fileName: string
  fileSize: number
  calculatedSha256: string
  type: ArtifactType
  schemaVersion?: number | string
  parseStatus: 'SUCCESS' | 'PARSE_FAILED' | 'UNKNOWN_TYPE'
  errorMessage?: string
  rawJson?: Record<string, unknown>
  validationLevel?: 'UNKNOWN' | 'RECOGNIZED_INVALID' | 'VALID_SCHEMA' | 'SELF_HASH_VERIFIED'
  calculatedSelfSha256?: string
  selfHashField?: string
  rawText?: string
  verifiedAgainstParent?: boolean
  validationIssues?: string[]
}

export interface ChainNodeState {
  id: string
  name: string
  expectedArtifactType: ArtifactType
  artifact?: ParsedArtifact
  status: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID'
  claimedSha?: string
  actualSha?: string
  upstreamOk: boolean
  notes?: string
}

export type EventCategory =
  | 'SYSTEM'
  | 'COLLECTION'
  | 'ARCHIVE'
  | 'DQ'
  | 'EVIDENCE'
  | 'RESEARCH'
  | 'SAFETY'

export type Severity = 'INFO' | 'WARN' | 'ERROR' | 'CRITICAL'

export interface OpsEvent {
  id: string
  time: string
  category: EventCategory
  severity: Severity
  title: string
  detail: string
  source: 'UI DERIVED' | 'RUNTIME EVIDENCE' | 'DECLARED'
}

export interface StreamMetric {
  exchange: string
  stream: string
  status: Status
  latestEvent: string
  p50: string
  p95: string
  reconnects: string
  queueDrops: string
  dataRate: string
  metricType: EvidenceSourceLabel
  note?: string
}

export interface ProjectStateSummary {
  projectMode: string
  soak72hStatus: 'PENDING' | 'PASS' | 'FAIL' | 'NOT LOADED'
  realDqStatus: 'NOT RUN' | 'PASS' | 'DEGRADED' | 'FAIL'
  alphaStatus: 'UNPROVEN'
  holdoutStatus: 'NOT CREATED' | 'SEALED' | 'CONSUMED'
  paperStatus: 'NOT STARTED'
  liveTradingStatus: 'DISABLED'
  privateApiStatus: 'DISABLED'
  offlineTooling: 'MERGED TO MAIN'
  syntheticVerification: 'PASS'
}
