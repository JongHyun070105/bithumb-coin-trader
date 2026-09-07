import type { OpsEvent, ParsedArtifact, PipelineStage, ProjectStateSummary, StreamMetric } from '../types'

export const DEMO_PROJECT_SUMMARY: ProjectStateSummary = {
  projectMode: 'OFFLINE RESEARCH',
  soak72hStatus: 'PASS',
  realDqStatus: 'PASS',
  alphaStatus: 'UNPROVEN',
  holdoutStatus: 'SEALED',
  paperStatus: 'NOT STARTED',
  liveTradingStatus: 'DISABLED',
  privateApiStatus: 'DISABLED',
  offlineTooling: 'MERGED TO MAIN',
  syntheticVerification: 'PASS'
}

export const DEMO_PIPELINE_STAGES: PipelineStage[] = [
  { id: 'infra', label: '1. 인프라 배포', status: 'COMPLETE', detail: 'AWS EC2 t3.medium / 200GB gp3 격리 봉인' },
  { id: 'collection', label: '2. 72H 수집', status: 'COMPLETE', detail: '259,200초 무인 자율 구동 (76개 피드)' },
  { id: 'seal', label: '3. 증거 봉인', status: 'COMPLETE', detail: '에포크 계약 및 런타임 씰 암호학적 서명' },
  { id: 'deep_dq', label: '4. 심층 DQ 감사', status: 'COMPLETE', detail: '타임스탬프 비역전성 및 무결성 100% 전수 검증' },
  { id: 'canonical', label: '5. 캐노니컬 변환', status: 'COMPLETE', detail: '단일 시계열 나노초 정렬 및 미등록 원시 차단' },
  { id: 'dataset', label: '6. 데이터셋 분할', status: 'COMPLETE', detail: 'Discovery 24h / Validation 24h / Holdout 22h' },
  { id: 'discovery', label: '7. 탐색 연구', status: 'NOT_STARTED', detail: '가설 사전등록 기반 피처 발굴 (대기 중)' },
  { id: 'validation', label: '8. 가설 검증', status: 'NOT_STARTED', detail: 'DSR/PBO/WRC 다중 가설 패널티 검정' },
  { id: 'holdout', label: '9. 홀드아웃', status: 'BLOCKED', detail: '암호학적 완전 봉인 유지 (비열람 원칙)' },
  { id: 'paper', label: '10. 모의 트레이딩', status: 'NOT_STARTED', detail: '실거래 안전 가드 전면 차단 상태' },
  { id: 'live', label: '11. 라이브 실행', status: 'BLOCKED', detail: '실거래 파이프라인 비활성화 (전송 계층 잠금)' }
]

export const NO_EVIDENCE_PROJECT_SUMMARY: ProjectStateSummary = {
  projectMode: 'OFFLINE RESEARCH',
  soak72hStatus: 'PENDING',
  realDqStatus: 'NOT RUN',
  alphaStatus: 'UNPROVEN',
  holdoutStatus: 'NOT CREATED',
  paperStatus: 'NOT STARTED',
  liveTradingStatus: 'DISABLED',
  privateApiStatus: 'DISABLED',
  offlineTooling: 'MERGED TO MAIN',
  syntheticVerification: 'PASS'
}

export const NO_EVIDENCE_PIPELINE_STAGES: PipelineStage[] = [
  { id: 'infra', label: '1. 인프라 배포', status: 'COMPLETE', detail: 'AWS 프로덕션 환경 격리 구동 중' },
  { id: 'collection', label: '2. 72H 수집', status: 'RUNNING', detail: '독립 무인 수집 진행 중 (259,200초 목표)' },
  { id: 'seal', label: '3. 증거 봉인', status: 'PENDING', detail: '실제 시작/종료 증거 수신 대기' },
  { id: 'deep_dq', label: '4. 심층 DQ 감사', status: 'NOT_STARTED', detail: '원시 데이터 인계 후 실행 대기' },
  { id: 'canonical', label: '5. 캐노니컬 변환', status: 'NOT_STARTED', detail: 'DQ 적격 승인 후 실행' },
  { id: 'dataset', label: '6. 데이터셋 분할', status: 'NOT_STARTED', detail: '캐노니컬 완료 후 분할' },
  { id: 'discovery', label: '7. 탐색 연구', status: 'NOT_STARTED', detail: '데이터셋 생성 후 착수' },
  { id: 'validation', label: '8. 가설 검증', status: 'NOT_STARTED', detail: '사전등록 규약에 따라 실행' },
  { id: 'holdout', label: '9. 홀드아웃', status: 'BLOCKED', detail: '홀드아웃 미생성 (봉인 대기)' },
  { id: 'paper', label: '10. 모의 트레이딩', status: 'NOT_STARTED', detail: '실거래 안전 가드 전면 차단 상태' },
  { id: 'live', label: '11. 라이브 실행', status: 'BLOCKED', detail: '실거래 파이프라인 비활성화' }
]

export const DEMO_STREAMS: StreamMetric[] = [
  { exchange: 'bithumb', stream: 'orderbook', status: 'HEALTHY', latestEvent: '2.1 ms 전', p50: '1.8 ms', p95: '4.2 ms', reconnects: '0', queueDrops: '0', dataRate: '128.4 msg/s', metricType: 'SYNTHETIC' },
  { exchange: 'bithumb', stream: 'trade', status: 'HEALTHY', latestEvent: '14.0 ms 전', p50: '1.2 ms', p95: '3.1 ms', reconnects: '0', queueDrops: '0', dataRate: '42.1 msg/s', metricType: 'SYNTHETIC' },
  { exchange: 'bithumb', stream: 'ticker', status: 'HEALTHY', latestEvent: '85.2 ms 전', p50: '2.5 ms', p95: '5.0 ms', reconnects: '0', queueDrops: '0', dataRate: '10.0 msg/s', metricType: 'SYNTHETIC' },
  { exchange: 'binance', stream: 'orderbook', status: 'HEALTHY', latestEvent: '5.1 ms 전', p50: '2.1 ms', p95: '6.8 ms', reconnects: '1', queueDrops: '0', dataRate: '210.3 msg/s', metricType: 'SYNTHETIC' },
  { exchange: 'binance', stream: 'trade', status: 'HEALTHY', latestEvent: '9.2 ms 전', p50: '1.5 ms', p95: '4.0 ms', reconnects: '1', queueDrops: '0', dataRate: '85.6 msg/s', metricType: 'SYNTHETIC' },
  { exchange: 'upbit', stream: 'orderbook', status: 'HEALTHY', latestEvent: '3.4 ms 전', p50: '1.6 ms', p95: '3.9 ms', reconnects: '0', queueDrops: '0', dataRate: '95.2 msg/s', metricType: 'SYNTHETIC' },
  { exchange: 'upbit', stream: 'trade', status: 'HEALTHY', latestEvent: '11.0 ms 전', p50: '1.4 ms', p95: '3.3 ms', reconnects: '0', queueDrops: '0', dataRate: '38.0 msg/s', metricType: 'SYNTHETIC' }
]

export const DEMO_EVENTS: OpsEvent[] = [
  { id: 'ev-1', time: '2026-09-05T02:40:39Z', category: 'COLLECTION', severity: 'INFO', title: '72H 무인 수집 세션 기동', detail: '런타임 씰 및 런칭 출처 파일 생성 완료 (PID 4821)', source: 'UI DERIVED' },
  { id: 'ev-2', time: '2026-09-05T02:40:40Z', category: 'COLLECTION', severity: 'INFO', title: '실제 프로세스 시작 증거 수집', detail: 'systemd journal 및 소켓 수신 개시 타임스탬프 기록 완료', source: 'UI DERIVED' },
  { id: 'ev-3', time: '2026-09-05T03:00:00Z', category: 'ARCHIVE', severity: 'INFO', title: '1차 정시 롤링 아카이브 봉인', detail: 'archive_receipt_20260905_03.json SHA-256 서명 완료', source: 'UI DERIVED' },
  { id: 'ev-4', time: '2026-09-08T02:40:40Z', category: 'COLLECTION', severity: 'INFO', title: '72시간 무인 수집 자연 완료', detail: '누적 259,200초 경과, 76개 피드 전수 스트리밍 수집 성공', source: 'UI DERIVED' },
  { id: 'ev-5', time: '2026-09-08T03:10:00Z', category: 'EVIDENCE', severity: 'INFO', title: '에포크 계약서 합성 및 루트 봉인', detail: 'epoch_contract.json 및 epoch_manifest.json 생성', source: 'UI DERIVED' },
  { id: 'ev-6', time: '2026-09-08T03:30:00Z', category: 'DQ', severity: 'INFO', title: '심층 DQ 감사 통과 (DQ_PASS)', detail: '하드 블로커 0건, 타임스탬프 역전 0건, 품질저하 0건 판정', source: 'UI DERIVED' },
  { id: 'ev-7', time: '2026-09-08T04:00:00Z', category: 'RESEARCH', severity: 'INFO', title: '연구용 데이터셋 생성 완료', detail: 'Discovery 24h / Validation 24h 분할, Holdout 22h 봉인', source: 'UI DERIVED' }
]

export const DEMO_ARTIFACTS_RAW = {
  runtime_seal: {
    schema_version: 1,
    runtime_software_commit: 'e9e4be4db086706e57ba51c14a2432a106526fc8',
    runtime_fingerprint: '3a8f90c12d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a',
    duration_seconds: 259200,
    feeds: {
      bithumb_markets: ['KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL', 'KRW-DOGE', 'KRW-ADA', 'KRW-AVAX', 'KRW-DOT', 'KRW-MATIC', 'KRW-LINK', 'KRW-SHIB', 'KRW-TRX', 'KRW-BCH', 'KRW-UNI', 'KRW-NEAR', 'KRW-ATOM', 'KRW-ETC', 'KRW-FIL', 'KRW-APT', 'KRW-SUI'],
      binance_symbols: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT'],
      upbit_markets: ['KRW-BTC', 'KRW-ETH', 'KRW-SOL', 'KRW-XRP']
    }
  },
  launch_provenance: {
    schema_version: 1,
    collector_epoch: 'epoch-aws-72h-soak-20260905',
    collector_run_id: 'run-aws-72h-soak-20260905-8017b83e',
    runtime_code_commit: 'e9e4be4db086706e57ba51c14a2432a106526fc8',
    runtime_fingerprint: '3a8f90c12d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a',
    created_at_utc: '2026-09-05T02:40:39Z',
    launch_command: 'scripts/run_cross_market_collector.py --strict-soak'
  },
  actual_start_evidence: {
    schema_version: 1,
    collector_epoch: 'epoch-aws-72h-soak-20260905',
    collector_run_id: 'run-aws-72h-soak-20260905-8017b83e',
    actual_start_time_utc: '2026-09-05T02:40:40.124590Z',
    start_evidence_type: 'SYSTEMD_SERVICE_START',
    source: 'journalctl_systemd_start',
    runtime_commit: 'e9e4be4db086706e57ba51c14a2432a106526fc8',
    runtime_fingerprint: '3a8f90c12d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a',
    captured_at_utc: '2026-09-05T02:40:41Z'
  },
  epoch_contract: {
    schema_version: 1,
    contract_type: 'OFFICIAL_72H_SOAK_CONTRACT',
    collector_epoch: 'epoch-aws-72h-soak-20260905',
    collector_run_id: 'run-aws-72h-soak-20260905-8017b83e',
    runtime_software_commit: 'e9e4be4db086706e57ba51c14a2432a106526fc8',
    runtime_fingerprint: '3a8f90c12d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a',
    actual_start_time_utc: '2026-09-05T02:40:40.124590Z',
    expected_duration_sec: 259200,
    expected_end_time_utc: '2026-09-08T02:40:40.124590Z',
    feed_universe: { total_feeds: 76, bithumb: 60, binance: 8, upbit: 8 },
    contract_sha256: '7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d'
  },
  epoch_manifest: {
    schema_version: 1,
    epoch_manifest_sha256: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
    collector_epoch: 'epoch-aws-72h-soak-20260905',
    collector_run_id: 'run-aws-72h-soak-20260905-8017b83e',
    status: 'SEALED_COMPLETE',
    sealed_complete: true,
    partitions: [
      { stream: 'orderbook', market: 'KRW-BTC', exchange: 'bithumb', total_records: 5421000 },
      { stream: 'trade', market: 'KRW-BTC', exchange: 'bithumb', total_records: 1245000 }
    ],
    cohorts: { expected: 73, present: 73, missing: 0 }
  },
  deep_dq_report: {
    schema_version: 1,
    audit_type: '72H_SOAK_DEEP_AUDIT',
    report_title: '72H Soak Full Streaming Deep DQ Audit Report',
    auditor_version: '2.0.0',
    overall_status: 'PASS',
    blockers: [],
    degraded: [],
    feed_coverage: { expected_feeds: 76, verified_feeds: 76, missing_feeds: [] },
    timestamp_integrity: { monotonic_reversals: 0, malformed_timestamps: 0 },
    record_integrity: { corrupt_records: 0, envelope_errors: 0 }
  },
  dq_qualification: {
    schema_version: 1,
    qualification_type: 'DEEP_DQ_QUALIFICATION',
    status: 'DQ_PASS',
    epoch_manifest_sha256: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
    audit_report_sha256: 'd4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5'
  },
  canonical_manifest: {
    schema_version: 1,
    canonical_manifest_sha256: 'e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6',
    source_epoch_manifest_sha256: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
    canonicalizer_commit: 'e9e4be4db086706e57ba51c14a2432a106526fc8',
    canonical_partitions: [{ market: 'KRW-BTC', exchange: 'bithumb', stream: 'orderbook' }]
  },
  dataset_manifest: {
    schema_version: 1,
    dataset_id: 'dataset-krw-btc-20260905-v1',
    source_epoch_id: 'epoch-aws-72h-soak-20260905',
    source_run_id: 'run-aws-72h-soak-20260905-8017b83e',
    canonical_manifest_sha256: 'e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6',
    splits: {
      train_hours: 24,
      embargo_1_hours: 2,
      validation_hours: 24,
      embargo_2_hours: 2,
      holdout_hours: 22,
      holdout_state: 'SEALED'
    }
  }
}

export function generateSyntheticArtifacts(): ParsedArtifact[] {
  return [
    {
      id: 'demo-art-1',
      fileName: 'aws-72h-soak-20260905.runtime.json',
      fileSize: 4210,
      calculatedSha256: '3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a',
      type: 'runtime_seal',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.runtime_seal
    },
    {
      id: 'demo-art-2',
      fileName: 'aws-72h-soak-20260905.launch-provenance.json',
      fileSize: 1890,
      calculatedSha256: '5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b',
      type: 'launch_provenance',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.launch_provenance
    },
    {
      id: 'demo-art-3',
      fileName: 'actual_start_evidence.json',
      fileSize: 1240,
      calculatedSha256: '6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c',
      type: 'actual_start_evidence',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.actual_start_evidence
    },
    {
      id: 'demo-art-4',
      fileName: 'epoch_contract.json',
      fileSize: 3120,
      calculatedSha256: '7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d',
      type: 'epoch_contract',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.epoch_contract
    },
    {
      id: 'demo-art-5',
      fileName: 'epoch_manifest.json',
      fileSize: 18450,
      calculatedSha256: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2',
      type: 'epoch_manifest',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.epoch_manifest
    },
    {
      id: 'demo-art-6',
      fileName: 'deep_dq_report.json',
      fileSize: 8920,
      calculatedSha256: 'd4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5',
      type: 'deep_dq_report',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.deep_dq_report
    },
    {
      id: 'demo-art-7',
      fileName: 'dq_qualification.json',
      fileSize: 1540,
      calculatedSha256: 'c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4',
      type: 'dq_qualification',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.dq_qualification
    },
    {
      id: 'demo-art-8',
      fileName: 'canonical_manifest.json',
      fileSize: 12400,
      calculatedSha256: 'e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6',
      type: 'canonical_manifest',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.canonical_manifest
    },
    {
      id: 'demo-art-9',
      fileName: 'dataset_manifest.json',
      fileSize: 4500,
      calculatedSha256: 'f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7',
      type: 'dataset_manifest',
      schemaVersion: 1,
      parseStatus: 'SUCCESS',
      rawJson: DEMO_ARTIFACTS_RAW.dataset_manifest
    }
  ]
}
