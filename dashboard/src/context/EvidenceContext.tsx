import React, { useReducer } from 'react'
import {
  EvidenceContext,
  evidenceReducer,
  initialState,
  type EvidenceContextValue
} from './evidenceContextDef'
import type { ParsedArtifact } from '../types'

export const EvidenceProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, dispatch] = useReducer(evidenceReducer, initialState)

  const loadDemo = () => dispatch({ type: 'LOAD_DEMO' })
  const clearEvidence = () => dispatch({ type: 'CLEAR_EVIDENCE' })
  const addArtifacts = (artifacts: ParsedArtifact[]) => dispatch({ type: 'ADD_ARTIFACTS', payload: artifacts })
  const removeArtifact = (id: string) => dispatch({ type: 'REMOVE_ARTIFACT', payload: id })

  const contextValue: EvidenceContextValue = {
    ...state,
    loadDemo,
    clearEvidence,
    addArtifacts,
    removeArtifact
  }

  return (
    <EvidenceContext.Provider value={contextValue}>
      {children}
    </EvidenceContext.Provider>
  )
}
