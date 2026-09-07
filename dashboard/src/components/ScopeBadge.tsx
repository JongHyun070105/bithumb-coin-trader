import React from 'react'
import type { ScopeBadgeType } from '../types'

interface ScopeBadgeProps {
  scope: ScopeBadgeType
}

export const ScopeBadge: React.FC<ScopeBadgeProps> = ({ scope }) => {
  let cls = 'scope-badge-na'
  if (scope === 'FULL') cls = 'scope-badge-full'
  if (scope === 'SAMPLED') cls = 'scope-badge-sampled'
  if (scope === 'MANIFEST-DERIVED') cls = 'scope-badge-derived'

  return <span className={`scope-badge ${cls}`}>{scope}</span>
}
