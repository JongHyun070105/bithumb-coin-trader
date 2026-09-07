import type {
  ArtifactType,
  ChainNodeState,
  ChainOverallState,
  ParsedArtifact,
} from '../types'
export interface EvaluationResult {
  nodes: ChainNodeState[]
  overallState: ChainOverallState
  summaryMessage: string
  issues: string[]
}
export function uniqueArtifacts(
  artifacts: ParsedArtifact[],
): Map<ArtifactType, ParsedArtifact> {
  const result = new Map<ArtifactType, ParsedArtifact>()
  for (const a of artifacts)
    if (
      a.type !== 'unknown' &&
      artifacts.filter((b) => b.type === a.type).length === 1 &&
      a.parseStatus === 'SUCCESS' &&
      (a.validationLevel === 'VALID_SCHEMA' ||
        a.validationLevel === 'SELF_HASH_VERIFIED')
    )
      result.set(a.type, a)
  return result
}
export function evaluateEvidenceChain(
  artifacts: ParsedArtifact[],
): EvaluationResult {
  const issues: string[] = []
  const grouped = new Map<ArtifactType, ParsedArtifact[]>()
  for (const a of artifacts)
    if (a.type !== 'unknown')
      grouped.set(a.type, [...(grouped.get(a.type) ?? []), a])
  const duplicates = [...grouped]
    .filter(([, v]) => v.length > 1)
    .map(([k]) => k)
  const get = (t: ArtifactType) =>
    grouped.get(t)?.length === 1 ? grouped.get(t)![0] : undefined
  const seal = get('runtime_seal'),
    launch = get('launch_provenance'),
    start = get('actual_start_evidence'),
    contract = get('epoch_contract'),
    root = get('epoch_manifest'),
    audit = get('deep_dq_report'),
    qual = get('dq_qualification'),
    canonical = get('canonical_manifest'),
    dataset = get('dataset_manifest')
  const j = (a: ParsedArtifact | undefined) => a?.rawJson ?? {}
  const valid = (a: ParsedArtifact | undefined) =>
    !!a &&
    a.parseStatus === 'SUCCESS' &&
    (a.validationLevel === 'VALID_SCHEMA' ||
      a.validationLevel === 'SELF_HASH_VERIFIED')
  const differs = (a: unknown, b: unknown) =>
    a !== undefined && b !== undefined && a !== b
  const same = (a: unknown, b: unknown) =>
    typeof a === 'string' && a.length > 0 && a === b
  const nodes: ChainNodeState[] = []
  function node(
    id: string,
    name: string,
    type: ArtifactType,
    a: ParsedArtifact | undefined,
    mismatch: boolean,
    acceptable: boolean,
    upstream: boolean,
    notes: string,
  ) {
    const status: ChainNodeState['status'] = !a
      ? 'MISSING'
      : mismatch
        ? 'MISMATCH'
        : !valid(a) || !acceptable
          ? 'INVALID'
          : 'PRESENT'
    if (status === 'MISMATCH' || status === 'INVALID')
      issues.push(name + ': ' + status)
    nodes.push({
      id,
      name,
      expectedArtifactType: type,
      artifact: a,
      status,
      upstreamOk: upstream,
      claimedSha: a?.selfHashField ? String(j(a)[a.selfHashField]) : undefined,
      actualSha: a?.calculatedSelfSha256,
      notes,
    })
  }
  const lc = j(launch).runtime_code_commit,
    fp = j(launch).runtime_config_fingerprint ?? j(launch).fingerprint
  const identityMismatch =
    ['collector_epoch', 'collector_run_id'].some((k) =>
      differs(j(start)[k], j(launch)[k]),
    ) ||
    differs(j(start).runtime_commit, lc) ||
    differs(j(start).runtime_fingerprint, fp) ||
    differs(j(seal).runtime_software_commit, lc) ||
    differs(
      j(seal).runtime_config_fingerprint ?? j(seal).runtime_fingerprint,
      fp,
    )
  const provenance =
    valid(seal) &&
    valid(launch) &&
    valid(start) &&
    same(j(start).collector_epoch, j(launch).collector_epoch) &&
    same(j(start).collector_run_id, j(launch).collector_run_id) &&
    same(j(start).runtime_commit, lc) &&
    same(j(start).runtime_fingerprint, fp) &&
    same(j(seal).runtime_software_commit, lc)
  const sealRef = j(launch).runtime_config_seal_sha256
  node(
    'node-provenance',
    '런타임 / 실제 시작 신원',
    'runtime_seal',
    seal ?? launch ?? start,
    identityMismatch || differs(sealRef, seal?.calculatedSha256),
    provenance,
    true,
    '신원 대조 및 로컬 파일 참조. 외부 진위 인증은 미수행.',
  )
  const contractMismatch =
    differs(j(contract).runtime_seal_sha256, seal?.calculatedSha256) ||
    differs(j(contract).launch_provenance_sha256, launch?.calculatedSha256) ||
    differs(
      j(contract).actual_start_evidence_file_sha256,
      start?.calculatedSha256,
    ) ||
    ['collector_epoch', 'collector_run_id'].some((k) =>
      differs(j(contract)[k], j(launch)[k]),
    ) ||
    differs(j(contract).runtime_software_commit, lc) ||
    differs(j(contract).runtime_fingerprint, fp) ||
    (valid(start) &&
      valid(contract) &&
      Date.parse(String(j(start).actual_start_time_utc)) !==
        Date.parse(String(j(contract).actual_start_time_utc)))
  node(
    'node-contract',
    '에포크 계약',
    'epoch_contract',
    contract,
    contractMismatch,
    true,
    provenance && !identityMismatch,
    'SELF HASH + upstream FILE BYTE SHA256',
  )
  const rootMismatch =
    differs(j(root).contract_sha256, j(contract).contract_sha256) ||
    differs(j(root).contract_file_sha256, contract?.calculatedSha256) ||
    differs(j(root).runtime_seal_sha256, seal?.calculatedSha256) ||
    differs(j(root).launch_provenance_sha256, launch?.calculatedSha256) ||
    ['collector_epoch', 'collector_run_id', 'runtime_fingerprint'].some((k) =>
      differs(j(root)[k], j(contract)[k]),
    ) ||
    differs(j(root).runtime_commit, j(contract).runtime_software_commit)
  node(
    'node-epoch-root',
    '에포크 루트',
    'epoch_manifest',
    root,
    rootMismatch,
    j(root).status === 'SEALED_COMPLETE' &&
      j(root).sealed_complete === true &&
      Array.isArray(j(root).missing_items) &&
      (j(root).missing_items as unknown[]).length === 0,
    valid(contract),
    'SELF HASH + contract canonical / file digest. RAW bytes not inspected.',
  )
  const warnings = j(audit).warnings
  const cleanAudit =
    j(audit).audit_type === 'authoritative_deep_dq' &&
    j(audit).status === 'DQ_PASS_ELIGIBLE' &&
    Array.isArray(j(audit).blockers) &&
    (j(audit).blockers as unknown[]).length === 0 &&
    Array.isArray(warnings) &&
    warnings.every((w) => typeof w === 'string' && w.startsWith('INFO:')) &&
    (!('errors' in j(audit)) ||
      (Array.isArray(j(audit).errors) &&
        (j(audit).errors as unknown[]).length === 0)) &&
    ['unknown_count', 'hard_fail_count', 'degraded_count'].every(
      (k) => !(k in j(audit)) || j(audit)[k] === 0,
    )
  node(
    'node-deep-dq',
    '심층 DQ 보고서',
    'deep_dq_report',
    audit,
    differs(j(audit).epoch_manifest_sha256, j(root).epoch_manifest_sha256),
    cleanAudit,
    valid(root),
    '보고된 DQ 상태 및 root digest 대조. RAW 재감사는 미수행.',
  )
  const qualMismatch =
    differs(j(qual).audit_report_sha256, audit?.calculatedSha256) ||
    differs(j(qual).epoch_manifest_sha256, j(root).epoch_manifest_sha256) ||
    differs(j(qual).source_manifest_hash, j(root).epoch_manifest_sha256) ||
    differs(j(qual).source_manifest_file_sha256, root?.calculatedSha256)
  const cleanQual =
    j(qual).status === 'DQ_PASS' &&
    ['hard_fail_count', 'unknown_count', 'degraded_count'].every(
      (k) => j(qual)[k] === 0,
    ) &&
    j(qual).auditor_version === 'v9.1.0-offline' &&
    j(qual).criteria_version === 'v1-strict' &&
    j(qual).approved_policy === 'strict_v1' &&
    same(j(qual).audit_code_commit, j(qual).auditor_commit)
  node(
    'node-dq-qual',
    'DQ 적격성',
    'dq_qualification',
    qual,
    qualMismatch,
    cleanQual,
    valid(audit) && cleanAudit && valid(root),
    'SELF HASH + audit FILE BYTE SHA256 + root canonical / file digest',
  )
  node(
    'node-canonical-root',
    '캐노니컬 루트',
    'canonical_manifest',
    canonical,
    differs(
      j(canonical).source_epoch_manifest_sha256,
      j(root).epoch_manifest_sha256,
    ) ||
      differs(
        j(canonical).dq_qualification_sha256,
        j(qual).qualification_sha256,
      ),
    true,
    valid(root) && valid(qual) && cleanQual,
    'SELF HASH + root / qualification canonical digest. Partition bytes not inspected.',
  )
  const datasetMismatch =
    differs(
      j(dataset).canonical_manifest_sha256,
      j(canonical).canonical_manifest_sha256,
    ) ||
    differs(j(dataset).dq_qualification_sha256, j(qual).qualification_sha256) ||
    differs(j(dataset).epoch_manifest_sha256, j(root).epoch_manifest_sha256) ||
    differs(j(dataset).deep_dq_report_sha256, audit?.calculatedSha256) ||
    [
      ['source_epoch_id', 'collector_epoch'],
      ['source_run_id', 'collector_run_id'],
      ['source_runtime_commit', 'runtime_commit'],
      ['source_runtime_fingerprint', 'runtime_fingerprint'],
    ].some(([a, b]) => differs(j(dataset)[a], j(root)[b]))
  node(
    'node-dataset-root',
    '데이터셋 메타데이터',
    'dataset_manifest',
    dataset,
    datasetMismatch,
    j(dataset).dq_status === 'DQ_PASS',
    valid(canonical) && valid(qual) && valid(root) && valid(audit),
    'UPSTREAM references only; no dataset self-hash contract. Holdout not opened.',
  )
  let overallState: ChainOverallState = 'NOT ENOUGH EVIDENCE'
  if (duplicates.length) {
    overallState = 'AMBIGUOUS_EVIDENCE'
    issues.push('Duplicate authoritative types: ' + duplicates.join(', '))
  } else if (nodes.some((n) => n.status === 'MISMATCH'))
    overallState = 'MISMATCH'
  else if (nodes.some((n) => n.status === 'INVALID')) overallState = 'INVALID'
  else if (nodes.every((n) => n.status === 'PRESENT' && n.upstreamOk))
    overallState = 'STRUCTURALLY COMPLETE'
  else if (artifacts.length) overallState = 'INCOMPLETE'
  return {
    nodes,
    overallState,
    issues,
    summaryMessage:
      overallState === 'STRUCTURALLY COMPLETE'
        ? '메타데이터 구조·자체 해시·참조 대조 완료. RAW/partition 및 외부 진위 검증은 미완료.'
        : '증거 검증 상태: ' + overallState,
  }
}
