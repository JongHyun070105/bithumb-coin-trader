import { useContext } from 'react'
import { EvidenceContext, type EvidenceContextValue } from './evidenceContextDef'

export function useEvidence(): EvidenceContextValue {
  const ctx = useContext(EvidenceContext)
  if (!ctx) {
    throw new Error('useEvidence must be used within an EvidenceProvider')
  }
  return ctx
}
