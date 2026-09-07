import { parseRawJsonToArtifact } from '../evidence/artifactClassifier'
import type {
  OpsEvent,
  ParsedArtifact,
  PipelineStage,
  ProjectStateSummary,
  StreamMetric,
} from '../types'

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
  syntheticVerification: 'PASS',
}

export const DEMO_PIPELINE_STAGES: PipelineStage[] = [
  {
    id: 'infra',
    label: '1. 인프라 배포',
    status: 'COMPLETE',
    detail: 'AWS EC2 t3.medium / 200GB gp3 격리 봉인',
  },
  {
    id: 'collection',
    label: '2. 72H 수집',
    status: 'COMPLETE',
    detail: '259,200초 무인 자율 구동 (76개 피드)',
  },
  {
    id: 'seal',
    label: '3. 증거 봉인',
    status: 'COMPLETE',
    detail: '에포크 계약 및 런타임 씰 자체 해시',
  },
  {
    id: 'deep_dq',
    label: '4. 심층 DQ 감사',
    status: 'COMPLETE',
    detail: '타임스탬프 비역전성 및 무결성 100% 전수 검증',
  },
  {
    id: 'canonical',
    label: '5. 캐노니컬 변환',
    status: 'COMPLETE',
    detail: '단일 시계열 나노초 정렬 및 미등록 원시 차단',
  },
  {
    id: 'dataset',
    label: '6. 데이터셋 분할',
    status: 'COMPLETE',
    detail: 'Discovery 24h / Validation 24h / Holdout 22h',
  },
  {
    id: 'discovery',
    label: '7. 탐색 연구',
    status: 'NOT_STARTED',
    detail: '가설 사전등록 기반 피처 발굴 (대기 중)',
  },
  {
    id: 'validation',
    label: '8. 가설 검증',
    status: 'NOT_STARTED',
    detail: 'DSR/PBO/WRC 다중 가설 패널티 검정',
  },
  {
    id: 'holdout',
    label: '9. 홀드아웃',
    status: 'BLOCKED',
    detail: '암호학적 완전 봉인 유지 (비열람 원칙)',
  },
  {
    id: 'paper',
    label: '10. 모의 트레이딩',
    status: 'NOT_STARTED',
    detail: '실거래 안전 가드 전면 차단 상태',
  },
  {
    id: 'live',
    label: '11. 라이브 실행',
    status: 'BLOCKED',
    detail: '실거래 파이프라인 비활성화 (전송 계층 잠금)',
  },
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
  syntheticVerification: 'PASS',
}

export const NO_EVIDENCE_PIPELINE_STAGES: PipelineStage[] = [
  {
    id: 'infra',
    label: '1. 인프라 배포',
    status: 'COMPLETE',
    detail: 'AWS 프로덕션 환경 격리 구동 중',
  },
  {
    id: 'collection',
    label: '2. 72H 수집',
    status: 'RUNNING',
    detail: '독립 무인 수집 진행 중 (259,200초 목표)',
  },
  {
    id: 'seal',
    label: '3. 증거 봉인',
    status: 'PENDING',
    detail: '실제 시작/종료 증거 수신 대기',
  },
  {
    id: 'deep_dq',
    label: '4. 심층 DQ 감사',
    status: 'NOT_STARTED',
    detail: '원시 데이터 인계 후 실행 대기',
  },
  {
    id: 'canonical',
    label: '5. 캐노니컬 변환',
    status: 'NOT_STARTED',
    detail: 'DQ 적격 승인 후 실행',
  },
  {
    id: 'dataset',
    label: '6. 데이터셋 분할',
    status: 'NOT_STARTED',
    detail: '캐노니컬 완료 후 분할',
  },
  {
    id: 'discovery',
    label: '7. 탐색 연구',
    status: 'NOT_STARTED',
    detail: '데이터셋 생성 후 착수',
  },
  {
    id: 'validation',
    label: '8. 가설 검증',
    status: 'NOT_STARTED',
    detail: '사전등록 규약에 따라 실행',
  },
  {
    id: 'holdout',
    label: '9. 홀드아웃',
    status: 'BLOCKED',
    detail: '홀드아웃 미생성 (봉인 대기)',
  },
  {
    id: 'paper',
    label: '10. 모의 트레이딩',
    status: 'NOT_STARTED',
    detail: '실거래 안전 가드 전면 차단 상태',
  },
  {
    id: 'live',
    label: '11. 라이브 실행',
    status: 'BLOCKED',
    detail: '실거래 파이프라인 비활성화',
  },
]

export const DEMO_STREAMS: StreamMetric[] = [
  {
    exchange: 'bithumb',
    stream: 'orderbook',
    status: 'HEALTHY',
    latestEvent: '2.1 ms 전',
    p50: '1.8 ms',
    p95: '4.2 ms',
    reconnects: '0',
    queueDrops: '0',
    dataRate: '128.4 msg/s',
    metricType: 'SYNTHETIC',
  },
  {
    exchange: 'bithumb',
    stream: 'trade',
    status: 'HEALTHY',
    latestEvent: '14.0 ms 전',
    p50: '1.2 ms',
    p95: '3.1 ms',
    reconnects: '0',
    queueDrops: '0',
    dataRate: '42.1 msg/s',
    metricType: 'SYNTHETIC',
  },
  {
    exchange: 'bithumb',
    stream: 'ticker',
    status: 'HEALTHY',
    latestEvent: '85.2 ms 전',
    p50: '2.5 ms',
    p95: '5.0 ms',
    reconnects: '0',
    queueDrops: '0',
    dataRate: '10.0 msg/s',
    metricType: 'SYNTHETIC',
  },
  {
    exchange: 'binance',
    stream: 'orderbook',
    status: 'HEALTHY',
    latestEvent: '5.1 ms 전',
    p50: '2.1 ms',
    p95: '6.8 ms',
    reconnects: '1',
    queueDrops: '0',
    dataRate: '210.3 msg/s',
    metricType: 'SYNTHETIC',
  },
  {
    exchange: 'binance',
    stream: 'trade',
    status: 'HEALTHY',
    latestEvent: '9.2 ms 전',
    p50: '1.5 ms',
    p95: '4.0 ms',
    reconnects: '1',
    queueDrops: '0',
    dataRate: '85.6 msg/s',
    metricType: 'SYNTHETIC',
  },
  {
    exchange: 'upbit',
    stream: 'orderbook',
    status: 'HEALTHY',
    latestEvent: '3.4 ms 전',
    p50: '1.6 ms',
    p95: '3.9 ms',
    reconnects: '0',
    queueDrops: '0',
    dataRate: '95.2 msg/s',
    metricType: 'SYNTHETIC',
  },
  {
    exchange: 'upbit',
    stream: 'trade',
    status: 'HEALTHY',
    latestEvent: '11.0 ms 전',
    p50: '1.4 ms',
    p95: '3.3 ms',
    reconnects: '0',
    queueDrops: '0',
    dataRate: '38.0 msg/s',
    metricType: 'SYNTHETIC',
  },
]

export const DEMO_EVENTS: OpsEvent[] = [
  {
    id: 'ev-1',
    time: '2026-09-05T02:40:39Z',
    category: 'COLLECTION',
    severity: 'INFO',
    title: '72H 무인 수집 세션 기동',
    detail: '런타임 씰 및 런칭 출처 파일 생성 완료 (PID 4821)',
    source: 'UI DERIVED',
  },
  {
    id: 'ev-2',
    time: '2026-09-05T02:40:40Z',
    category: 'COLLECTION',
    severity: 'INFO',
    title: '실제 프로세스 시작 증거 수집',
    detail: 'systemd journal 및 소켓 수신 개시 타임스탬프 기록 완료',
    source: 'UI DERIVED',
  },
  {
    id: 'ev-3',
    time: '2026-09-05T03:00:00Z',
    category: 'ARCHIVE',
    severity: 'INFO',
    title: '1차 정시 롤링 아카이브 봉인',
    detail: 'archive_receipt_20260905_03.json SHA-256 계산 완료',
    source: 'UI DERIVED',
  },
  {
    id: 'ev-4',
    time: '2026-09-08T02:40:40Z',
    category: 'COLLECTION',
    severity: 'INFO',
    title: '72시간 무인 수집 자연 완료',
    detail: '누적 259,200초 경과, 76개 피드 전수 스트리밍 수집 성공',
    source: 'UI DERIVED',
  },
  {
    id: 'ev-5',
    time: '2026-09-08T03:10:00Z',
    category: 'EVIDENCE',
    severity: 'INFO',
    title: '에포크 계약서 합성 및 루트 봉인',
    detail: 'epoch_contract.json 및 epoch_manifest.json 생성',
    source: 'UI DERIVED',
  },
  {
    id: 'ev-6',
    time: '2026-09-08T03:30:00Z',
    category: 'DQ',
    severity: 'INFO',
    title: '심층 DQ 감사 통과 (DQ_PASS)',
    detail: '하드 블로커 0건, 타임스탬프 역전 0건, 품질저하 0건 판정',
    source: 'UI DERIVED',
  },
  {
    id: 'ev-7',
    time: '2026-09-08T04:00:00Z',
    category: 'RESEARCH',
    severity: 'INFO',
    title: '연구용 데이터셋 생성 완료',
    detail: 'Discovery 24h / Validation 24h 분할, Holdout 22h 봉인',
    source: 'UI DERIVED',
  },
]

const goldenTexts = import.meta.glob('../../tests/golden/*.json', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>
export function generateSyntheticArtifacts(): ParsedArtifact[] {
  return Object.entries(goldenTexts)
    .filter(
      ([path]) =>
        !path.endsWith('/hashes.json') &&
        !path.endsWith('/canonical_vectors.json'),
    )
    .map(([path, text], i) =>
      parseRawJsonToArtifact(
        'synthetic-' + i,
        path.split('/').pop()!,
        new TextEncoder().encode(text).length,
        '',
        text,
      ),
    )
}
