import { createContext } from 'react'
import type {
  DashboardMode,
  OpsEvent,
  ParsedArtifact,
  PipelineStage,
  ProjectStateSummary,
  StreamMetric,
} from '../types'
import {
  evaluateEvidenceChain,
  type EvaluationResult,
} from '../evidence/chainEvaluator'
import {
  DEMO_EVENTS,
  DEMO_PIPELINE_STAGES,
  DEMO_PROJECT_SUMMARY,
  DEMO_STREAMS,
  generateSyntheticArtifacts,
  NO_EVIDENCE_PIPELINE_STAGES,
  NO_EVIDENCE_PROJECT_SUMMARY,
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
  events: [],
}

export function deriveProjectState(artifacts: ParsedArtifact[]): {
  summary: ProjectStateSummary
  stages: PipelineStage[]
  events: OpsEvent[]
  streams: StreamMetric[]
} {
  // Imported metadata does not change the project's scientific/live status.
  return {
    summary: { ...NO_EVIDENCE_PROJECT_SUMMARY, soak72hStatus: 'PENDING' },
    stages: NO_EVIDENCE_PIPELINE_STAGES,
    events: artifacts.map((a) => ({
      id: 'ev-' + a.id,
      time: new Date().toISOString(),
      category: 'EVIDENCE' as const,
      severity: 'INFO' as const,
      title: 'Metadata imported: ' + a.fileName,
      detail: a.validationLevel ?? 'NOT VERIFIED',
      source: 'UI DERIVED' as const,
    })),
    streams: [],
  }
}

export function evidenceReducer(
  state: EvidenceState,
  action: EvidenceAction,
): EvidenceState {
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
        events: DEMO_EVENTS,
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
        events: [],
      }
    }
    case 'ADD_ARTIFACTS': {
      const updated = [
        ...(state.mode === 'SYNTHETIC_DEMO' ? [] : state.artifacts),
        ...action.payload,
      ]
      const evaluation = evaluateEvidenceChain(updated)
      const derived = deriveProjectState(updated)
      return {
        mode: 'IMPORTED_EVIDENCE',
        artifacts: updated,
        chainEvaluation: evaluation,
        projectSummary: derived.summary,
        pipelineStages: derived.stages,
        streams: derived.streams,
        events: derived.events.slice(0, 50),
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
          events: [],
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
        streams: derived.streams,
      }
    }
    default:
      return state
  }
}

export const EvidenceContext = createContext<EvidenceContextValue | undefined>(
  undefined,
)
