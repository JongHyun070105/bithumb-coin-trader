import type { ArtifactType, ParsedArtifact } from '../types'
import { canonicalJson } from './canonicalJson'
import { sha256Text } from './hashCalculator'
export const MAX_FILE_SIZE = 10 * 1024 * 1024
const object = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
const string = (v: unknown) => typeof v === 'string' && v.length > 0
const digest = (v: unknown) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v)
const count = (v: unknown) =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= 0
const fields = (
  j: Record<string, unknown>,
  names: string[],
  test = (v: unknown) => string(v),
) => names.every((k) => test(j[k]))
export const SELF_FIELDS: Partial<Record<ArtifactType, string>> = {
  epoch_contract: 'contract_sha256',
  epoch_manifest: 'epoch_manifest_sha256',
  dq_qualification: 'qualification_sha256',
  canonical_manifest: 'canonical_manifest_sha256',
}
export function classifyArtifact(
  _fileName: string,
  j: Record<string, unknown>,
): { type: ArtifactType; schemaVersion?: string | number } {
  let type: ArtifactType = 'unknown'
  if (
    'contract_type' in j ||
    ('contract_file_sha256' in j && !('partitions' in j))
  )
    type = 'epoch_contract'
  else if ('qualification_sha256' in j || 'approved_policy' in j)
    type = 'dq_qualification'
  else if ('dataset_id' in j) type = 'dataset_manifest'
  else if ('canonical_manifest_sha256' in j) type = 'canonical_manifest'
  else if ('epoch_manifest_sha256' in j && 'partitions' in j)
    type = 'epoch_manifest'
  else if ('actual_start_time_utc' in j || 'start_evidence_type' in j)
    type = 'actual_start_evidence'
  else if ('feeds' in j && 'runtime_software_commit' in j) type = 'runtime_seal'
  else if ('runtime_code_commit' in j && 'collector_run_id' in j)
    type = 'launch_provenance'
  else if ('audit_type' in j) type = 'deep_dq_report'
  else if ('partition' in j && 'run_id' in j && 'compression_algorithm' in j)
    type = 'archive_receipt'
  else if ('scan' in j && 'integrity' in j) type = 'fullscan_report'
  // Ancillary receipts/fullscans are schema-only, never DQ qualification.
  return {
    type,
    schemaVersion:
      typeof j.schema_version === 'number' ||
      typeof j.schema_version === 'string'
        ? j.schema_version
        : undefined,
  }
}
export function validSchema(
  type: ArtifactType,
  j: Record<string, unknown>,
): boolean {
  const identity = () => fields(j, ['collector_epoch', 'collector_run_id'])
  switch (type) {
    case 'runtime_seal':
      return (
        j.schema_version === 1 &&
        string(j.runtime_software_commit) &&
        object(j.feeds) &&
        ['bithumb_markets', 'binance_symbols', 'upbit_markets'].every((k) =>
          Array.isArray((j.feeds as Record<string, unknown>)[k]),
        )
      )
    case 'launch_provenance':
      return (
        (j.schema_version === undefined || j.schema_version === 1) &&
        identity() &&
        string(j.runtime_code_commit) &&
        string(j.runtime_config_fingerprint ?? j.fingerprint)
      )
    case 'actual_start_evidence':
      return (
        j.schema_version === 1 &&
        identity() &&
        fields(j, [
          'runtime_commit',
          'runtime_fingerprint',
          'source',
          'actual_start_time_utc',
          'captured_at_utc',
        ]) &&
        [
          'SYSTEMD_SERVICE_START',
          'PROCESS_EXEC_START',
          'FIRST_RAW_RECORD',
        ].includes(String(j.start_evidence_type)) &&
        ['actual_start_time_utc', 'captured_at_utc'].every(
          (k) =>
            /T.*(?:Z|[+-]\d\d:\d\d)$/.test(String(j[k])) &&
            Number.isFinite(Date.parse(String(j[k]))),
        )
      )
    case 'epoch_contract':
      return (
        j.schema_version === 1 &&
        j.contract_type === 'OFFICIAL_72H_SOAK_CONTRACT' &&
        identity() &&
        fields(j, [
          'runtime_software_commit',
          'runtime_fingerprint',
          'actual_start_time_utc',
          'start_time_utc',
          'expected_end_time_utc',
        ]) &&
        fields(
          j,
          [
            'contract_sha256',
            'runtime_seal_sha256',
            'launch_provenance_sha256',
            'actual_start_evidence_file_sha256',
          ],
          digest,
        ) &&
        j.feed_count === 76 &&
        Array.isArray(j.feed_universe) &&
        j.feed_universe.length === 76 &&
        count(j.duration_seconds) &&
        Number(j.duration_seconds) > 0
      )
    case 'epoch_manifest':
      return (
        j.schema_version === '2.1.0' &&
        identity() &&
        fields(j, ['runtime_commit', 'runtime_fingerprint']) &&
        fields(
          j,
          [
            'epoch_manifest_sha256',
            'contract_sha256',
            'contract_file_sha256',
            'runtime_seal_sha256',
            'launch_provenance_sha256',
          ],
          digest,
        ) &&
        Array.isArray(j.partitions) &&
        j.partitions_count === j.partitions.length &&
        Array.isArray(j.missing_items) &&
        typeof j.sealed_complete === 'boolean'
      )
    case 'deep_dq_report':
      return (
        j.schema_version === undefined &&
        j.audit_type === 'authoritative_deep_dq' &&
        fields(j, ['status', 'audited_at_utc']) &&
        digest(j.epoch_manifest_sha256) &&
        Array.isArray(j.blockers) &&
        Array.isArray(j.warnings) &&
        object(j.summary) &&
        object(j.manifest_verification) &&
        object(j.feed_coverage)
      )
    case 'dq_qualification':
      return (
        j.schema_version === undefined &&
        fields(j, [
          'status',
          'auditor_version',
          'auditor_commit',
          'audit_code_commit',
          'criteria_version',
          'approved_policy',
          'created_at',
        ]) &&
        fields(
          j,
          ['hard_fail_count', 'unknown_count', 'degraded_count'],
          count,
        ) &&
        fields(
          j,
          [
            'qualification_sha256',
            'report_hash',
            'source_manifest_hash',
            'source_manifest_file_sha256',
            'epoch_manifest_sha256',
            'audit_report_sha256',
          ],
          digest,
        )
      )
    case 'canonical_manifest':
      return (
        j.schema_version === '2.1.0' &&
        fields(
          j,
          [
            'canonical_manifest_sha256',
            'source_epoch_manifest_sha256',
            'dq_qualification_sha256',
          ],
          digest,
        ) &&
        string(j.canonicalizer_commit) &&
        Array.isArray(j.partitions) &&
        j.partitions_count === j.partitions.length
      )
    case 'dataset_manifest':
      return (
        j.schema_version === undefined &&
        fields(
          j,
          [
            'dataset_id',
            'epoch_manifest_sha256',
            'deep_dq_report_sha256',
            'dq_qualification_sha256',
            'canonical_manifest_sha256',
          ],
          digest,
        ) &&
        fields(j, [
          'source_epoch_id',
          'source_run_id',
          'source_runtime_commit',
          'source_runtime_fingerprint',
          'canonicalizer_commit',
          'dataset_builder_commit',
          'dq_status',
        ]) &&
        object(j.partitions) &&
        fields(
          j,
          [
            'source_record_count',
            'train_records',
            'validation_records',
            'holdout_records',
          ],
          count,
        )
      )
    case 'archive_receipt':
      return (
        j.schema_version === 1 &&
        fields(j, [
          'state',
          'environment_id',
          'run_id',
          'collector_epoch',
          'partition',
        ]) &&
        j.compression_algorithm === 'zstd' &&
        typeof j.cleanup_eligible === 'boolean'
      )
    case 'fullscan_report':
      return (
        j.schema_version === undefined &&
        fields(j, ['scan', 'epoch', 'hour']) &&
        object(j.integrity) &&
        object(j.integrity.totals) &&
        fields(j.integrity.totals, ['status']) &&
        Array.isArray(j.integrity.failures) &&
        object(j.quarantine)
      )
    default:
      return false
  }
}
export function parseRawJsonToArtifact(
  id: string,
  fileName: string,
  fileSize: number,
  _calculatedSha256: string,
  rawText: string,
): ParsedArtifact {
  const base: ParsedArtifact = {
    id,
    fileName,
    fileSize,
    calculatedSha256: '',
    type: 'unknown',
    parseStatus: 'PARSE_FAILED',
    validationLevel: 'UNKNOWN',
  }
  try {
    if (
      fileSize > MAX_FILE_SIZE ||
      new TextEncoder().encode(rawText).length > MAX_FILE_SIZE
    )
      throw Error('Metadata exceeds 10 MiB')
    canonicalJson(rawText) // bounded nesting, duplicate keys, lossless numeric tokens
    const j: unknown = JSON.parse(rawText)
    if (!object(j)) throw Error('JSON root must be an object')
    const { type, schemaVersion } = classifyArtifact(fileName, j)
    const a: ParsedArtifact = {
      ...base,
      type,
      schemaVersion,
      rawJson: j,
      rawText,
      calculatedSha256: sha256Text(rawText),
      parseStatus: type === 'unknown' ? 'UNKNOWN_TYPE' : 'SUCCESS',
      validationLevel: 'UNKNOWN',
      validationIssues: [],
    }
    if (type === 'unknown') return a
    if (!validSchema(type, j))
      return {
        ...a,
        parseStatus: 'PARSE_FAILED',
        validationLevel: 'RECOGNIZED_INVALID',
        errorMessage: 'Unsupported or invalid evidence schema',
      }
    a.validationLevel = 'VALID_SCHEMA'
    const self = SELF_FIELDS[type]
    if (self) {
      a.selfHashField = self
      a.calculatedSelfSha256 = sha256Text(
        canonicalJson(
          rawText,
          type === 'dq_qualification'
            ? ['report_hash', 'qualification_sha256']
            : [self],
        ),
      )
      if (
        a.calculatedSelfSha256 !== j[self] ||
        (type === 'dq_qualification' &&
          j.report_hash !== j.qualification_sha256)
      )
        return {
          ...a,
          validationLevel: 'RECOGNIZED_INVALID',
          errorMessage: 'SELF_HASH_MISMATCH',
        }
      a.validationLevel = 'SELF_HASH_VERIFIED'
    }
    return a
  } catch (e) {
    return {
      ...base,
      errorMessage: e instanceof Error ? e.message : 'Invalid JSON',
      validationIssues: ['Invalid metadata input'],
    }
  }
}
