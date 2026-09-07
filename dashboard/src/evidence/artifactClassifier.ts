import type { ArtifactType, ParsedArtifact } from '../types'

export function classifyArtifact(
  fileName: string,
  json: Record<string, unknown>
): { type: ArtifactType; schemaVersion?: string | number } {
  // 1. Explicit contract_type or schema fields
  if (json.contract_type === 'OFFICIAL_72H_SOAK_CONTRACT' || (json.contract_sha256 && json.feed_universe)) {
    return { type: 'epoch_contract', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.qualification_type === 'DEEP_DQ_QUALIFICATION' || (json.epoch_manifest_sha256 && json.audit_report_sha256 && json.status)) {
    return { type: 'dq_qualification', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.canonical_manifest_sha256 && json.canonical_partitions) {
    return { type: 'canonical_manifest', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.dataset_id && json.splits && json.source_epoch_id) {
    return { type: 'dataset_manifest', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.epoch_manifest_sha256 && json.partitions && json.cohorts) {
    return { type: 'epoch_manifest', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.actual_start_time_utc || json.start_evidence_type) {
    return { type: 'actual_start_evidence', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.runtime_software_commit && json.feeds && json.runtime_fingerprint) {
    return { type: 'runtime_seal', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if ((json.launch_command || json.runtime_code_commit) && json.collector_epoch && json.collector_run_id) {
    return { type: 'launch_provenance', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.report_title?.toString().includes('72H') || json.audit_type === '72H_SOAK_DEEP_AUDIT' || (json.feed_coverage && json.timestamp_integrity)) {
    return { type: 'deep_dq_report', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.receipt_type === 'HOURLY_ARCHIVE_RECEIPT' || (json.archive_hour && json.files)) {
    return { type: 'archive_receipt', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  if (json.fullscan_hour && json.scanned_records) {
    return { type: 'fullscan_report', schemaVersion: (json.schema_version as number) ?? 1 }
  }

  // Filename heuristics fallback
  const lowerName = fileName.toLowerCase()
  if (lowerName.includes('contract')) return { type: 'epoch_contract' }
  if (lowerName.includes('epoch_manifest') || lowerName.includes('root_manifest')) return { type: 'epoch_manifest' }
  if (lowerName.includes('runtime') && lowerName.includes('seal')) return { type: 'runtime_seal' }
  if (lowerName.includes('launch') && lowerName.includes('provenance')) return { type: 'launch_provenance' }
  if (lowerName.includes('actual_start')) return { type: 'actual_start_evidence' }
  if (lowerName.includes('dq') && lowerName.includes('qual')) return { type: 'dq_qualification' }
  if (lowerName.includes('audit') || lowerName.includes('dq_report')) return { type: 'deep_dq_report' }
  if (lowerName.includes('canonical')) return { type: 'canonical_manifest' }
  if (lowerName.includes('dataset')) return { type: 'dataset_manifest' }
  if (lowerName.includes('receipt')) return { type: 'archive_receipt' }
  if (lowerName.includes('fullscan')) return { type: 'fullscan_report' }

  return { type: 'unknown' }
}

export function parseRawJsonToArtifact(
  id: string,
  fileName: string,
  fileSize: number,
  calculatedSha256: string,
  rawText: string
): ParsedArtifact {
  try {
    const rawJson = JSON.parse(rawText) as Record<string, unknown>
    const { type, schemaVersion } = classifyArtifact(fileName, rawJson)
    return {
      id,
      fileName,
      fileSize,
      calculatedSha256,
      type,
      schemaVersion,
      parseStatus: type === 'unknown' ? 'UNKNOWN_TYPE' : 'SUCCESS',
      rawJson,
      validationIssues: []
    }
  } catch (err) {
    return {
      id,
      fileName,
      fileSize,
      calculatedSha256,
      type: 'unknown',
      parseStatus: 'PARSE_FAILED',
      errorMessage: err instanceof Error ? err.message : 'JSON parse error',
      validationIssues: ['유효한 JSON 포맷이 아닙니다.']
    }
  }
}
