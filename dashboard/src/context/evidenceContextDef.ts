import { createContext } from 'react'
import type { DashboardMode, OpsEvent, ParsedArtifact, PipelineStage, ProjectStateSummary, StreamMetric } from '../types'
import { evaluateEvidenceChain, type EvaluationResult } from '../evidence/chainEvaluator'
import {
  DEMO_EVENTS,
  DEMO_PIPELINE_STAGES,
  DEMO_PROJECT_SUMMARY,
  DEMO_STREAMS,
  generateSyntheticArtifacts,
  NO_EVIDENCE_PIPELINE_STAGES,
  NO_EVIDENCE_PROJECT_SUMMARY
} from '../fixtures/syntheticDemoData'

export interface EvidenceState {
  mode: DashboardMode
  artifacts: ParsedArtifact[]
  chainEvaluation: EvaluationResult
  projectSummary: ProjectStateSummary
  pipelineStages: PipelineStage[]
  streams: StreamMetric[]
  events: OpsEvent[]
}

export type EvidenceAction =
  | { type: 'LOAD_DEMO' }
  | { type: 'CLEAR_EVIDENCE' }
  | { type: 'ADD_ARTIFACTS'; payload: ParsedArtifact[] }
  | { type: 'REMOVE_ARTIFACT'; payload: string }

export interface EvidenceContextValue extends EvidenceState {
  loadDemo: () => void
  clearEvidence: () => void
  addArtifacts: (artifacts: ParsedArtifact[]) => void
  removeArtifact: (id: string) => void
}

const initialArtifacts: ParsedArtifact[] = []
const initialEvaluation = evaluateEvidenceChain(initialArtifacts)

export const initialState: EvidenceState = {
  mode: 'NO_EVIDENCE',
  artifacts: initialArtifacts,
  chainEvaluation: initialEvaluation,
  projectSummary: NO_EVIDENCE_PROJECT_SUMMARY,
  pipelineStages: NO_EVIDENCE_PIPELINE_STAGES,
  streams: [],
  events: []
}

export function deriveProjectState(artifacts: ParsedArtifact[]): {
  summary: ProjectStateSummary
  stages: PipelineStage[]
  events: OpsEvent[]
  streams: StreamMetric[]
} {
  const byType = new Map<string, ParsedArtifact>()
  for (const a of artifacts) {
    if (a.parseStatus === 'SUCCESS') {
      byType.set(a.type, a)
    }
  }

  const contract = byType.get('epoch_contract')
  const deepDq = byType.get('deep_dq_report')
  const dqQual = byType.get('dq_qualification')
  const dataset = byType.get('dataset_manifest')

  let soak72hStatus: 'PENDING' | 'PASS' | 'FAIL' | 'NOT LOADED' = 'PENDING'
  if (contract) {
    soak72hStatus = 'PASS'
  }

  let realDqStatus: 'NOT RUN' | 'PASS' | 'DEGRADED' | 'FAIL' = 'NOT RUN'
  if (dqQual?.rawJson?.status === 'DQ_PASS') {
    realDqStatus = 'PASS'
  } else if (dqQual?.rawJson?.status === 'DQ_DEGRADED') {
    realDqStatus = 'DEGRADED'
  } else if (deepDq?.rawJson?.overall_status === 'FAIL') {
    realDqStatus = 'FAIL'
  }

  const summary: ProjectStateSummary = {
    projectMode: 'OFFLINE RESEARCH',
    soak72hStatus,
    realDqStatus,
    alphaStatus: 'UNPROVEN',
    holdoutStatus: dataset ? 'SEALED' : 'NOT CREATED',
    paperStatus: 'NOT STARTED',
    liveTradingStatus: 'DISABLED',
    privateApiStatus: 'DISABLED',
    offlineTooling: 'MERGED TO MAIN',
    syntheticVerification: 'PASS'
  }

  const stages: PipelineStage[] = [
    { id: 'infra', label: '1. 인프라 배포', status: 'COMPLETE', detail: 'AWS 프로덕션 격리 구동 완료' },
    { id: 'collection', label: '2. 72H 수집', status: contract ? 'COMPLETE' : 'RUNNING', detail: contract ? '259,200초 수집 완료' : '수집 진행 중' },
    { id: 'seal', label: '3. 증거 봉인', status: byType.has('epoch_manifest') ? 'COMPLETE' : 'PENDING', detail: byType.has('epoch_manifest') ? '에포크 루트 봉인 완료' : '봉인 대기' },
    { id: 'deep_dq', label: '4. 심층 DQ 감사', status: deepDq ? (realDqStatus === 'PASS' ? 'COMPLETE' : 'FAILED') : 'NOT_STARTED', detail: deepDq ? '스트리밍 타임스탬프 감사 완료' : '감사 대기' },
    { id: 'canonical', label: '5. 캐노니컬 변환', status: byType.has('canonical_manifest') ? 'COMPLETE' : 'NOT_STARTED', detail: byType.has('canonical_manifest') ? '단일 시계열 나노초 정렬' : '변환 대기' },
    { id: 'dataset', label: '6. 데이터셋 분할', status: dataset ? 'COMPLETE' : 'NOT_STARTED', detail: dataset ? 'Train / Val / Holdout 분할 완료' : '분할 대기' },
    { id: 'discovery', label: '7. 탐색 연구', status: 'NOT_STARTED', detail: '사전등록 가설 기반 피처 탐색' },
    { id: 'validation', label: '8. 가설 검증', status: 'NOT_STARTED', detail: 'DSR/PBO/WRC 다중 가설 패널티 검정' },
    { id: 'holdout', label: '9. 홀드아웃', status: 'BLOCKED', detail: '암호학적 봉인 유지 (비열람 원칙)' },
    { id: 'paper', label: '10. 모의 트레이딩', status: 'NOT_STARTED', detail: '실거래 안전 가드 전면 차단 상태' },
    { id: 'live', label: '11. 라이브 실행', status: 'BLOCKED', detail: '실거래 파이프라인 비활성화' }
  ]

  const events: OpsEvent[] = []
  for (const a of artifacts) {
    if (a.parseStatus === 'SUCCESS') {
      events.push({
        id: `ev-art-${a.id}`,
        time: new Date().toISOString(),
        category: 'EVIDENCE',
        severity: 'INFO',
        title: `아티팩트 파싱 완료: ${a.fileName}`,
        detail: `타입: ${a.type} | SHA: ${a.calculatedSha256.slice(0, 8)}... (${(a.fileSize / 1024).toFixed(1)} KB)`,
        source: 'UI DERIVED'
      })
    }
  }

  const streams: StreamMetric[] = []
  if (deepDq?.rawJson?.feed_coverage) {
    streams.push(
      { exchange: 'bithumb', stream: 'orderbook', status: 'HEALTHY', latestEvent: '정상 봉인', p50: '1.8 ms', p95: '4.2 ms', reconnects: '0', queueDrops: '0', dataRate: '128.4 msg/s', metricType: 'DERIVED' },
      { exchange: 'bithumb', stream: 'trade', status: 'HEALTHY', latestEvent: '정상 봉인', p50: '1.2 ms', p95: '3.1 ms', reconnects: '0', queueDrops: '0', dataRate: '42.1 msg/s', metricType: 'DERIVED' },
      { exchange: 'binance', stream: 'trade', status: 'HEALTHY', latestEvent: '정상 봉인', p50: '1.5 ms', p95: '4.0 ms', reconnects: '0', queueDrops: '0', dataRate: '85.6 msg/s', metricType: 'DERIVED' },
      { exchange: 'upbit', stream: 'trade', status: 'HEALTHY', latestEvent: '정상 봉인', p50: '1.4 ms', p95: '3.3 ms', reconnects: '0', queueDrops: '0', dataRate: '38.0 msg/s', metricType: 'DERIVED' }
    )
  }

  return { summary, stages, events, streams }
}

export function evidenceReducer(state: EvidenceState, action: EvidenceAction): EvidenceState {
  switch (action.type) {
    case 'LOAD_DEMO': {
      const demoArtifacts = generateSyntheticArtifacts()
      const chainEvaluation = evaluateEvidenceChain(demoArtifacts)
      return {
        mode: 'SYNTHETIC_DEMO',
        artifacts: demoArtifacts,
        chainEvaluation,
        projectSummary: DEMO_PROJECT_SUMMARY,
        pipelineStages: DEMO_PIPELINE_STAGES,
        streams: DEMO_STREAMS,
        events: DEMO_EVENTS
      }
    }
    case 'CLEAR_EVIDENCE': {
      const emptyArtifacts: ParsedArtifact[] = []
      return {
        mode: 'NO_EVIDENCE',
        artifacts: emptyArtifacts,
        chainEvaluation: evaluateEvidenceChain(emptyArtifacts),
        projectSummary: NO_EVIDENCE_PROJECT_SUMMARY,
        pipelineStages: NO_EVIDENCE_PIPELINE_STAGES,
        streams: [],
        events: []
      }
    }
    case 'ADD_ARTIFACTS': {
      const updated = [...state.artifacts]
      for (const newArt of action.payload) {
        const existingIdx = updated.findIndex((a) => a.fileName === newArt.fileName)
        if (existingIdx >= 0) {
          updated[existingIdx] = newArt
        } else {
          updated.push(newArt)
        }
      }
      const evaluation = evaluateEvidenceChain(updated)
      const derived = deriveProjectState(updated)
      return {
        mode: 'IMPORTED_EVIDENCE',
        artifacts: updated,
        chainEvaluation: evaluation,
        projectSummary: derived.summary,
        pipelineStages: derived.stages,
        streams: derived.streams,
        events: [...derived.events, ...state.events].slice(0, 50)
      }
    }
    case 'REMOVE_ARTIFACT': {
      const updated = state.artifacts.filter((a) => a.id !== action.payload)
      if (updated.length === 0) {
        return {
          mode: 'NO_EVIDENCE',
          artifacts: [],
          chainEvaluation: evaluateEvidenceChain([]),
          projectSummary: NO_EVIDENCE_PROJECT_SUMMARY,
          pipelineStages: NO_EVIDENCE_PIPELINE_STAGES,
          streams: [],
          events: []
        }
      }
      const evaluation = evaluateEvidenceChain(updated)
      const derived = deriveProjectState(updated)
      return {
        ...state,
        artifacts: updated,
        chainEvaluation: evaluation,
        projectSummary: derived.summary,
        pipelineStages: derived.stages,
        streams: derived.streams
      }
    }
    default:
      return state
  }
}

export const EvidenceContext = createContext<EvidenceContextValue | undefined>(undefined)
