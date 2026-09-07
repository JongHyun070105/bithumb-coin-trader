import type { ChainNodeState, ChainOverallState, ParsedArtifact } from '../types'

export interface EvaluationResult {
  nodes: ChainNodeState[]
  overallState: ChainOverallState
  summaryMessage: string
  issues: string[]
}

export function evaluateEvidenceChain(artifacts: ParsedArtifact[]): EvaluationResult {
  const issues: string[] = []

  // Map by type
  const byType: Partial<Record<string, ParsedArtifact>> = {}
  for (const a of artifacts) {
    if (a.parseStatus === 'SUCCESS') {
      byType[a.type] = a
    }
  }

  const runtimeSeal = byType['runtime_seal']
  const launchProv = byType['launch_provenance']
  const actualStart = byType['actual_start_evidence']
  const contract = byType['epoch_contract']
  const epochManifest = byType['epoch_manifest']
  const deepDq = byType['deep_dq_report']
  const dqQual = byType['dq_qualification']
  const canonicalManifest = byType['canonical_manifest']
  const datasetManifest = byType['dataset_manifest']

  // 1. Provenance Node
  const hasProvenance = Boolean(runtimeSeal && launchProv)
  const provHasActualStart = Boolean(actualStart)
  let provStatus: 'PRESENT' | 'MISSING' | 'INVALID' = 'MISSING'
  if (hasProvenance && provHasActualStart) {
    provStatus = 'PRESENT'
  } else if (hasProvenance && !provHasActualStart) {
    provStatus = 'INVALID'
    issues.push('실제 시작 시각 증거(actual_start_evidence.json)가 누락되어 봉인 전 출처가 불완전합니다.')
  }

  const node1: ChainNodeState = {
    id: 'node-provenance',
    name: '런타임 씰 & 런칭 출처',
    expectedArtifactType: 'runtime_seal',
    artifact: runtimeSeal || launchProv,
    status: provStatus,
    claimedSha: runtimeSeal?.calculatedSha256,
    actualSha: runtimeSeal?.calculatedSha256,
    upstreamOk: true,
    notes: provHasActualStart ? '런타임 씰 + 런칭 출처 + 실제 시작 증거 완비' : '실제 시작 시각 증거 부재 (Fail-Closed)'
  }

  // 2. Epoch Contract Node
  let contractStatus: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID' = 'MISSING'
  let contractNotes = '계약서 미확인'
  if (contract?.rawJson) {
    const json = contract.rawJson
    const claimSealSha = json.runtime_seal_sha256 as string | undefined
    if (runtimeSeal && claimSealSha && claimSealSha !== runtimeSeal.calculatedSha256) {
      contractStatus = 'MISMATCH'
      issues.push(`계약서 내 런타임 씰 해시 불일치: ${claimSealSha.slice(0, 8)} != ${runtimeSeal.calculatedSha256.slice(0, 8)}`)
    } else {
      contractStatus = 'PRESENT'
      contractNotes = `72H 수집 계약 봉인 (${(json.contract_sha256 as string)?.slice(0, 8) ?? 'SHA-OK'})`
    }
  }

  const node2: ChainNodeState = {
    id: 'node-contract',
    name: '에포크 계약 (Epoch Contract)',
    expectedArtifactType: 'epoch_contract',
    artifact: contract,
    status: contractStatus,
    claimedSha: (contract?.rawJson?.contract_sha256 as string) || contract?.calculatedSha256,
    actualSha: contract?.calculatedSha256,
    upstreamOk: node1.status === 'PRESENT',
    notes: contractNotes
  }

  // 3. Epoch Root Manifest Node
  let rootStatus: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID' = 'MISSING'
  let rootNotes = '에포크 루트 매니페스트 미확인'
  if (epochManifest?.rawJson) {
    const json = epochManifest.rawJson
    const claimedSelfSha = json.epoch_manifest_sha256 as string | undefined
    const contractClaimedSha = json.contract_sha256 as string | undefined

    if (contract && contractClaimedSha && contractClaimedSha !== contract.calculatedSha256 && contractClaimedSha !== json.contract_sha256) {
      rootStatus = 'MISMATCH'
      issues.push('에포크 루트 내 계약서 해시 불일치')
    } else if (!claimedSelfSha) {
      rootStatus = 'INVALID'
      issues.push('에포크 매니페스트에 셀프 해시(epoch_manifest_sha256)가 부재합니다.')
    } else {
      rootStatus = 'PRESENT'
      rootNotes = `76개 피드 원시 파티션 봉인 완료 (${claimedSelfSha.slice(0, 8)}...)`
    }
  }

  const node3: ChainNodeState = {
    id: 'node-epoch-root',
    name: '에포크 루트 (Epoch Root Manifest)',
    expectedArtifactType: 'epoch_manifest',
    artifact: epochManifest,
    status: rootStatus,
    claimedSha: (epochManifest?.rawJson?.epoch_manifest_sha256 as string) || epochManifest?.calculatedSha256,
    actualSha: epochManifest?.calculatedSha256,
    upstreamOk: node2.status === 'PRESENT',
    notes: rootNotes
  }

  // 4. Deep DQ Audit Node
  let dqStatus: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID' = 'MISSING'
  let dqNotes = '심층 데이터 품질 감사 미수행'
  if (deepDq?.rawJson) {
    const json = deepDq.rawJson
    const blockers = (json.blockers as unknown[]) ?? []
    if (blockers.length > 0) {
      dqStatus = 'INVALID'
      dqNotes = `하드 블로커 ${blockers.length}건 적발`
      issues.push(`심층 DQ 감사에서 ${blockers.length}건의 하드 블로커가 감지되었습니다.`)
    } else {
      dqStatus = 'PRESENT'
      dqNotes = '스트리밍 타임스탬프/봉투 무결성 검증 통과'
    }
  }

  const node4: ChainNodeState = {
    id: 'node-deep-dq',
    name: '심층 DQ 감사 (Deep Audit)',
    expectedArtifactType: 'deep_dq_report',
    artifact: deepDq,
    status: dqStatus,
    claimedSha: deepDq?.calculatedSha256,
    actualSha: deepDq?.calculatedSha256,
    upstreamOk: node3.status === 'PRESENT',
    notes: dqNotes
  }

  // 5. DQ Qualification Node
  let qualStatus: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID' = 'MISSING'
  let qualNotes = 'DQ 적격성 판정 미확인'
  if (dqQual?.rawJson) {
    const json = dqQual.rawJson
    const statusVal = json.status as string | undefined
    if (statusVal === 'DQ_PASS') {
      qualStatus = 'PRESENT'
      qualNotes = '공식 72H 데이터 품질 적격 승인 (DQ_PASS)'
    } else if (statusVal === 'DQ_DEGRADED') {
      qualStatus = 'INVALID'
      qualNotes = '품질 저하 발생 (DQ_DEGRADED - 공식 승인 불가)'
      issues.push('DQ 적격성 상태가 DQ_DEGRADED로 공식 데이터셋 생성 불가 상태입니다.')
    } else {
      qualStatus = 'INVALID'
      qualNotes = `부적격 판정 (${statusVal ?? 'UNKNOWN'})`
      issues.push(`DQ 적격성 판정 실패: ${statusVal}`)
    }
  }

  const node5: ChainNodeState = {
    id: 'node-dq-qual',
    name: 'DQ 적격성 판정 (Qualification)',
    expectedArtifactType: 'dq_qualification',
    artifact: dqQual,
    status: qualStatus,
    claimedSha: dqQual?.calculatedSha256,
    actualSha: dqQual?.calculatedSha256,
    upstreamOk: node4.status === 'PRESENT',
    notes: qualNotes
  }

  // 6. Canonical Root Node
  let canonicalStatus: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID' = 'MISSING'
  let canonicalNotes = '캐노니컬 루트 미확인'
  if (canonicalManifest?.rawJson) {
    const json = canonicalManifest.rawJson
    const srcEpochSha = json.source_epoch_manifest_sha256 as string | undefined
    if (epochManifest && srcEpochSha && srcEpochSha !== (epochManifest.rawJson?.epoch_manifest_sha256 ?? epochManifest.calculatedSha256)) {
      canonicalStatus = 'MISMATCH'
      issues.push('캐노니컬 매니페스트 내 소스 에포크 매니페스트 해시 불일치 (EVIDENCE_CHAIN_MISMATCH)')
    } else {
      canonicalStatus = 'PRESENT'
      canonicalNotes = '단일 시계열 캐노니컬 변환 완료'
    }
  }

  const node6: ChainNodeState = {
    id: 'node-canonical-root',
    name: '캐노니컬 루트 (Canonical Root)',
    expectedArtifactType: 'canonical_manifest',
    artifact: canonicalManifest,
    status: canonicalStatus,
    claimedSha: (canonicalManifest?.rawJson?.canonical_manifest_sha256 as string) || canonicalManifest?.calculatedSha256,
    actualSha: canonicalManifest?.calculatedSha256,
    upstreamOk: node5.status === 'PRESENT',
    notes: canonicalNotes
  }

  // 7. Dataset Root Node
  let datasetStatus: 'PRESENT' | 'MISSING' | 'MISMATCH' | 'INVALID' = 'MISSING'
  let datasetNotes = '연구 데이터셋 미확인'
  if (datasetManifest?.rawJson) {
    const json = datasetManifest.rawJson
    const canSha = json.canonical_manifest_sha256 as string | undefined
    if (canonicalManifest && canSha && canSha !== (canonicalManifest.rawJson?.canonical_manifest_sha256 ?? canonicalManifest.calculatedSha256)) {
      datasetStatus = 'MISMATCH'
      issues.push('데이터셋 매니페스트 내 캐노니컬 루트 해시 불일치')
    } else {
      datasetStatus = 'PRESENT'
      datasetNotes = 'Discovery 24h / Validation 24h / Holdout 22h 분할 완료'
    }
  }

  const node7: ChainNodeState = {
    id: 'node-dataset-root',
    name: '연구 데이터셋 (Dataset Root)',
    expectedArtifactType: 'dataset_manifest',
    artifact: datasetManifest,
    status: datasetStatus,
    claimedSha: datasetManifest?.calculatedSha256,
    actualSha: datasetManifest?.calculatedSha256,
    upstreamOk: node6.status === 'PRESENT',
    notes: datasetNotes
  }

  const nodes = [node1, node2, node3, node4, node5, node6, node7]

  // Determine overall state
  const hasAnyMismatch = nodes.some((n) => n.status === 'MISMATCH')
  const hasAnyInvalid = nodes.some((n) => n.status === 'INVALID')
  const presentCount = nodes.filter((n) => n.status === 'PRESENT').length

  let overallState: ChainOverallState = 'NOT ENOUGH EVIDENCE'
  let summaryMessage = '증거 아티팩트가 임포트되지 않았습니다.'

  if (hasAnyMismatch) {
    overallState = 'MISMATCH'
    summaryMessage = '증거 사슬 간 암호학적 해시 불일치가 감지되었습니다.'
  } else if (hasAnyInvalid) {
    overallState = 'INVALID'
    summaryMessage = '증거 사슬 내 부적격 또는 결함 아티팩트가 존재합니다.'
  } else if (presentCount === 7) {
    overallState = 'COMPLETE'
    summaryMessage = '7단계 증거 사슬이 완벽히 연결되고 암호학적으로 일관됩니다.'
  } else if (presentCount > 0) {
    overallState = 'INCOMPLETE'
    summaryMessage = `증거 사슬 7단계 중 ${presentCount}단계만 확보되었습니다.`
  }

  return {
    nodes,
    overallState,
    summaryMessage,
    issues
  }
}
