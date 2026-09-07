import { describe, it, expect } from 'vitest'
import { evaluateEvidenceChain } from './chainEvaluator'
import { parseRawJsonToArtifact } from './artifactClassifier'
import type { ParsedArtifact, ArtifactType } from '../types'
const art = (type: ArtifactType, rawJson: Record<string, unknown>): ParsedArtifact => ({id:type,fileName:type+'.json',fileSize:1,calculatedSha256:'a'.repeat(64),type,rawJson,parseStatus:'SUCCESS'})
const status = (a: ParsedArtifact[], id: string) => evaluateEvidenceChain(a).nodes.find(n=>n.id===id)?.status

describe('Gemini P0 regressions (untrusted declarations)',()=> {
  it('root contract mismatch',()=> expect(status([art('epoch_contract',{contract_sha256:'a'.repeat(64)}),art('epoch_manifest',{epoch_manifest_sha256:'b'.repeat(64),contract_sha256:'c'.repeat(64)})],'node-epoch-root')).toBe('MISMATCH'))
  it('forged root self hash',()=> expect(status([art('epoch_manifest',{epoch_manifest_sha256:'b'.repeat(64)})],'node-epoch-root')).toBe('INVALID'))
  it('DQ FAIL with empty blockers',()=> expect(status([art('deep_dq_report',{status:'FAIL',blockers:[]})],'node-deep-dq')).toBe('INVALID'))
  for(const field of ['degraded_count','unknown_count']) it('DQ_PASS with '+field,()=>expect(status([art('dq_qualification',{status:'DQ_PASS',[field]:1})],'node-dq-qual')).toBe('INVALID'))
  it('qualification wrong audit hash',()=>expect(status([art('deep_dq_report',{blockers:[]}),art('dq_qualification',{status:'DQ_PASS',audit_report_sha256:'b'.repeat(64)})],'node-dq-qual')).toBe('MISMATCH'))
  for(const name of ['evil_contract.json','audit-not-a-dq.json','dataset.json']) it('filename is hint only '+name,()=>expect(parseRawJsonToArtifact('x',name,2,'','{"hello":"world"}').parseStatus).not.toBe('SUCCESS'))
  for(const type of ['epoch_manifest','dq_qualification'] as const) it('duplicate '+type,()=>expect(evaluateEvidenceChain([art(type,{}),art(type,{})]).overallState).toBe('AMBIGUOUS_EVIDENCE'))
  it('provenance identity mismatch',()=>expect(status([art('runtime_seal',{}),art('launch_provenance',{collector_run_id:'run1'}),art('actual_start_evidence',{collector_run_id:'run2'})],'node-provenance')).toBe('MISMATCH'))
  it('no presence-only COMPLETE',()=>expect(evaluateEvidenceChain([art('runtime_seal',{}),art('launch_provenance',{}),art('actual_start_evidence',{}),art('epoch_contract',{}),art('epoch_manifest',{epoch_manifest_sha256:'b'.repeat(64)}),art('deep_dq_report',{blockers:[]}),art('dq_qualification',{status:'DQ_PASS'}),art('canonical_manifest',{}),art('dataset_manifest',{})]).overallState).not.toBe('COMPLETE'))
})
