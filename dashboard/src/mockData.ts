import type { OpsEvent, StreamMetric } from './types'

export const streamMetrics: StreamMetric[] = [
  { exchange: '빗썸', stream: '호가', status: 'HEALTHY', latestEvent: '0.3초 전', p50: '38 ms', p95: '91 ms', reconnects: '0', queueDrops: '검증 불가', dataRate: '12.4 msg/s', metricType: 'MEASURED' },
  { exchange: '빗썸', stream: '체결', status: 'HEALTHY', latestEvent: '0.7초 전', p50: '43 ms', p95: '104 ms', reconnects: '0', queueDrops: '검증 불가', dataRate: '3.2 msg/s', metricType: 'MEASURED' },
  { exchange: '빗썸', stream: '티커', status: 'HEALTHY', latestEvent: '0.2초 전', p50: '36 ms', p95: '88 ms', reconnects: '0', queueDrops: '검증 불가', dataRate: '5.0 msg/s', metricType: 'MEASURED' },
  { exchange: '바이낸스', stream: '호가', status: 'DEGRADED', latestEvent: '0.1초 전', p50: '52 ms', p95: '147 ms', reconnects: '1', queueDrops: '검증 불가', dataRate: '20.8 msg/s', metricType: 'MEASURED', note: 'V9 epoch에서 시장 symbol이 UNKNOWN으로 기록됐습니다.' },
  { exchange: '바이낸스', stream: '체결', status: 'DEGRADED', latestEvent: '0.4초 전', p50: '58 ms', p95: '171 ms', reconnects: '1', queueDrops: '검증 불가', dataRate: '4.7 msg/s', metricType: 'MEASURED', note: '중복 trade ID가 미해결 감사 finding으로 남아 있습니다.' },
  { exchange: '업비트', stream: '호가', status: 'HEALTHY', latestEvent: '0.2초 전', p50: '41 ms', p95: '96 ms', reconnects: '0', queueDrops: '검증 불가', dataRate: '13.1 msg/s', metricType: 'MEASURED' },
  { exchange: '업비트', stream: '체결', status: 'HEALTHY', latestEvent: '0.5초 전', p50: '47 ms', p95: '112 ms', reconnects: '0', queueDrops: '검증 불가', dataRate: '3.8 msg/s', metricType: 'MEASURED' },
]

export const events: OpsEvent[] = [
  { id: 'evt-001', time: '01:18:43', category: 'DATA', severity: 'WARN', title: '바이낸스 호가 symbol 미확인', detail: 'V9 raw records가 시장을 UNKNOWN으로 식별합니다. Finding을 유지합니다.' },
  { id: 'evt-002', time: '01:12:08', category: 'SYSTEM', severity: 'INFO', title: '수집기 heartbeat 관찰', detail: '설정된 모든 거래소 프로세스에서 최근 append 활동이 보고됐습니다.' },
  { id: 'evt-003', time: '00:54:31', category: 'RISK', severity: 'CRITICAL', title: '실거래 승격 게이트 잠김', detail: '데이터 무결성 finding 때문에 alpha-ready와 live-ready 승격을 차단합니다.' },
  { id: 'evt-004', time: '00:41:15', category: 'DATA', severity: 'ERROR', title: '수신 timestamp 역전 감지', detail: 'Local receive 순서 이상이 V9 인프라 감사 finding으로 남아 있습니다.' },
  { id: 'evt-005', time: '00:30:02', category: 'RESEARCH', severity: 'INFO', title: 'V9.1 안정화 계획 활성', detail: '새 epoch 변경은 기존 V9 수집기와 분리돼 있습니다.' },
  { id: 'evt-006', time: '00:18:44', category: 'TRADING', severity: 'INFO', title: '트레이딩 제어 비활성 유지', detail: '이 UI에는 계좌·주문·실거래 실행 연동이 없습니다.' },
  { id: 'evt-007', time: '23:57:09', category: 'SIGNAL', severity: 'WARN', title: '전략 신호 제공 안 됨', detail: '전략 검증이 끝나지 않아 운영 후보를 생성하지 않습니다.' },
]

export const safetyChecks = [
  { label: '신규 진입', value: '잠김', status: 'LOCKED' as const, detail: '승격 게이트가 닫혀 있습니다.' },
  { label: '실거래', value: '비활성', status: 'DISABLED' as const, detail: '실거래 제어를 제공하지 않습니다.' },
  { label: '계좌 대사', value: '제공 안 됨', status: 'UNKNOWN' as const, detail: '계좌 연동은 현재 범위 밖입니다.' },
  { label: '미확인 보유자산', value: '제공 안 됨', status: 'UNKNOWN' as const, detail: '계좌 데이터를 불러오지 않았습니다.' },
  { label: '데이터 상태', value: '저하', status: 'DEGRADED' as const, detail: 'V9 무결성 finding이 열려 있습니다.' },
  { label: 'Writer 상태', value: '알 수 없음', status: 'UNKNOWN' as const, detail: 'Durable metrics는 V9.1부터 시작됩니다.' },
  { label: 'Queue drop', value: '검증 불가', status: 'UNKNOWN' as const, detail: 'V9 epoch에는 durable counter가 없습니다.' },
  { label: '전략 검증', value: '아니오', status: 'FALSE' as const, detail: '전략 승격 근거가 없습니다.' },
  { label: '모의투자 검증', value: '아니오', status: 'FALSE' as const, detail: '모의투자 검증이 완료되지 않았습니다.' },
  { label: '실거래 준비', value: '아니오', status: 'FALSE' as const, detail: '읽기 전용 hard gate입니다.' },
]
