import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { parseRawJsonToArtifact } from './artifactClassifier'
import { evaluateEvidenceChain } from './chainEvaluator'
import { sha256Text } from './hashCalculator'
import { canonicalJson } from './canonicalJson'
const hashes = JSON.parse(
  readFileSync('tests/golden/hashes.json', 'utf8'),
) as Record<string, string>
const load = () =>
  Object.keys(hashes).map((type) => {
    const text = readFileSync('tests/golden/' + type + '.json', 'utf8')
    return parseRawJsonToArtifact(
      type,
      type + '.json',
      Buffer.byteLength(text),
      '',
      text,
    )
  })
describe('Exact Python producer bytes', () => {
  for (const type of Object.keys(hashes))
    it(type, () => {
      const a = load().find((a) => a.id === type)!
      expect(a.errorMessage).toBeUndefined()
      expect(a.type).toBe(type)
      expect(a.calculatedSha256).toBe(hashes[type])
      expect(a.errorMessage).toBeUndefined()
      expect(a.parseStatus).toBe('SUCCESS')
      if (a.selfHashField)
        expect(a.calculatedSelfSha256).toBe(a.rawJson?.[a.selfHashField])
    })
  it('metadata structural completeness is not RAW verification', () =>
    expect(evaluateEvidenceChain(load()).overallState).toBe(
      'STRUCTURALLY COMPLETE',
    ))
  const cases: [string, string, unknown, string, string][] = [
    [
      'epoch_manifest',
      'contract_sha256',
      '0'.repeat(64),
      'node-epoch-root',
      'MISMATCH',
    ],
    [
      'epoch_manifest',
      'epoch_manifest_sha256',
      '0'.repeat(64),
      'node-epoch-root',
      'INVALID',
    ],
    ['deep_dq_report', 'status', 'FAIL', 'node-deep-dq', 'INVALID'],
    ['deep_dq_report', 'audit_type', 'structural', 'node-deep-dq', 'INVALID'],
    [
      'deep_dq_report',
      'warnings',
      ['UNKNOWN: missing evidence'],
      'node-deep-dq',
      'INVALID',
    ],
    ['dq_qualification', 'degraded_count', 1, 'node-dq-qual', 'INVALID'],
    ['dq_qualification', 'unknown_count', 1, 'node-dq-qual', 'INVALID'],
    [
      'dq_qualification',
      'audit_report_sha256',
      '0'.repeat(64),
      'node-dq-qual',
      'MISMATCH',
    ],
    [
      'canonical_manifest',
      'source_epoch_manifest_sha256',
      '0'.repeat(64),
      'node-canonical-root',
      'MISMATCH',
    ],
    [
      'actual_start_evidence',
      'collector_run_id',
      'other-run',
      'node-provenance',
      'MISMATCH',
    ],
  ]
  for (const [type, field, value, node, expected] of cases)
    it('tamper ' + type + '.' + field, () => {
      const list = load()
      const index = list.findIndex((a) => a.id === type)
      const original = list[index]
      const j = { ...original.rawJson, [field]: value }
      // Reseal other modifications to isolate relationship/status checks from self-hash checking.
      if (original.selfHashField && field !== original.selfHashField) {
        const exclude =
          type === 'dq_qualification'
            ? ['report_hash', 'qualification_sha256']
            : [original.selfHashField]
        const hash = sha256Text(canonicalJson(JSON.stringify(j), exclude))
        j[original.selfHashField] = hash
        if (type === 'dq_qualification') j.report_hash = hash
      }
      const text = JSON.stringify(j)
      list[index] = parseRawJsonToArtifact(
        type,
        type + '.json',
        text.length,
        '',
        text,
      )
      expect(
        evaluateEvidenceChain(list).nodes.find((n) => n.id === node)?.status,
      ).toBe(expected)
    })
  it('missing actual start remains incomplete', () =>
    expect(
      evaluateEvidenceChain(
        load().filter((a) => a.type !== 'actual_start_evidence'),
      ).overallState,
    ).not.toBe('STRUCTURALLY COMPLETE'))
  for (const type of Object.keys(hashes))
    it('duplicate ' + type, () => {
      const list = load()
      list.push({ ...list.find((a) => a.id === type)!, id: 'duplicate' })
      expect(evaluateEvidenceChain(list).overallState).toBe(
        'AMBIGUOUS_EVIDENCE',
      )
    })
})
