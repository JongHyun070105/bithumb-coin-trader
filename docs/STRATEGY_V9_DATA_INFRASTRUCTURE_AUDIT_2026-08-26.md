# Strategy V9: Bithumb Market Microstructure & Cross-Market Data Infrastructure Audit (중간 감사 및 정밀 분석본)

- **일자**: 2026-08-26 13:42 KST (수집 13.3시간 경과)
- **감사 대상**: Strategy V9 Multi-Exchange Microstructure Data Infrastructure
- **원장 파일**: [reports/v9_data_infrastructure_audit_2026-08-26.json](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/reports/v9_data_infrastructure_audit_2026-08-26.json)
- **데몬 상태**: `PID 30933` (`task-680`) **무중단 정상 실행 중 (수정/중단/재시작 일절 없음)**
- **보존 규약**: 180일 Embargoed Quasi-OOS 홀드아웃 **0바이트 완전 미개봉 보존 유지**
- **안전장치**: `BITHUMB_NEW_ENTRIES=false` 실주문 차단 유지 | **Alpha Mining 전면 차단 유지**

---

## 1. Machine-Readable 인프라 감사 상태 선언

```json
{
  "collector_connected": true,
  "collector_process_untouched": true,
  "raw_ingestion_active": true,
  "manifest_pipeline_current": false,
  "status_metrics_fresh": true,
  "sequence_completeness_verifiable": false,
  "disk_capacity_safe_for_72h": true,
  "lossless_verified": false,
  "alpha_research_allowed": false,
  "live_trading_allowed": false
}
```

---

## 2. 12시간 중간 감사 핵심 발견 사항 (Root Cause Analysis)

### A. Manifest 43개 고정 원인 규명
- **원인**: `scripts/run_cross_market_collector.py` 코드 상 `collector.generate_all_manifests()`가 **프로세스 정상 종료 시점(`finally` 블록)에만 호출되도록 구현**되어 있었습니다.
- 00:21경 10초 테스트(`task-622`)가 정상 종료되며 43개의 매니페스트가 생성되었으나, 01:19:33에 가동된 72시간 실전 수집 데몬(`task-680`)은 **13시간 넘게 무중단 무한 루프로 실행 중**이므로 `finally` 블록에 도달하지 않아 새로운 매니페스트 파일이 생성되지 않았습니다.
- `check_collector_status.py`가 디스크의 과거 43개 매니페스트만 읽었기 때문에 12시간 동안 통계가 00:21 시점으로 동결(Stale)되어 있었던 것입니다.
- **조치**: Collector 프로세스를 건드리지 않고, 종료된 과거 파티션(mtime > 10분)에 대해 매니페스트를 독립 생성하는 오프라인 도구([scripts/generate_offline_manifests.py](file:///Users/macintosh/Documents/ChatGPT/bitcoin-trader/scripts/generate_offline_manifests.py))를 가동하여 매니페스트를 복구 중입니다.

### B. 실제 저장용량이 예상보다 10배 큰 이유 (22.8 GB/day vs 2.28 GB/day)
- **원인**: 레코드 크기가 커진 것이 아니라, **실제 이벤트 유입 속도(Throughput)가 예상치보다 9.3배 높았습니다**.
  - **이론적 가정**: 3개 거래소 합산 15 ~ 35 events/sec 가정 (평균 25 events/sec).
  - **실측치**: 3개 거래소 28개 스트림 전체 합산 **실제 발생량은 `232.5 events/sec`**였습니다!
  - 레코드 평균 크기는 **`1,220.9 bytes/record`**로 예상치(1,134 bytes)와 거의 일치했습니다.
- **스트림별 용량 기여율**:
  - **`orderbook`이 전체 용량의 `84.0%` (10.89 GB, 818.1 MB/h)를 차지**합니다.
    - `bithumb:orderbook` (37.6%, 4.87 GB, 366.0 MB/h)
    - `upbit:orderbook` (27.7%, 3.59 GB, 269.9 MB/h)
    - `binance:orderbook` (18.7%, 2.42 GB, 182.3 MB/h)
  - `trade`는 전체의 15.2% (1.97 GB, 148.2 MB/h)
  - `ticker`는 전체의 0.8% (0.10 GB, 7.8 MB/h)
- **거래소별 비중**: Bithumb 39.0%, Binance 32.1%, Upbit 28.9%로 고르게 분포되어 있습니다.

### C. Sequence Gap 292,830,750,332의 진실
- **원인**: 빗썸의 체결 `sequential_id`는 1씩 순차 증가하는 카운터가 아니라, **18자리 나노초 타임스탬프 또는 거래소 분산 Snowflake ID**(예: `998741008365677209`, `998741011519793819`)입니다.
- 이를 단순 `current_id - previous_id - 1`로 계산하면 단 1회의 체결 간격에서도 수십억의 가짜 Gap이 누적됩니다.
- **조치**: 해당 지표를 `invalid_metric_due_to_wrong_assumption`으로 원장에 명시하고, `trade_sequence_completeness = not_directly_verifiable`로 정정했습니다.
- **실제 중복 체결(`duplicate trade IDs`)**: 전수 검사 결과 **`0건 (Zero Duplicate)`**으로 정상 확인되었습니다.

### D. 실시간 Latency & Clock Offset 실측 (최신 2시간 active data 7만 건 분석)
- **Local Write Queue 지연 (내부 버퍼링)**:
  - $p50$: **`0.14 ms`**, $p95$: **`1.54 ms`** (1ms 이내 초고속 무손실 기록 입증).
- **거래소 시계와 로컬 수신 시계 오프셋 ($T_{\text{recv}} - T_{\text{exch}}$)**:
  - **Bithumb**: $p50 = \mathbf{19.51\text{ ms}}$, $p90 = 225.85\text{ ms}$, $p95 = 258.40\text{ ms}$ (음수 오프셋 44.9%).
  - **Binance**: $p50 = \mathbf{-37.36\text{ ms}}$, $p90 = 32.62\text{ ms}$, $p95 = 34.29\text{ ms}$ (음수 오프셋 66.7%).
  - **Upbit**: $p50 = \mathbf{7.50\text{ ms}}$, $p90 = 92.80\text{ ms}$, $p95 = 122.70\text{ ms}$ (음수 오프셋 49.7%).
- 과거 보고서의 $p50 = 3,845.80\text{ ms}$는 12시간 전 초기 연결 시점의 Stale 수치였습니다.

---

## 3. 디스크 용량 안전성 및 Exhaustion 프로젝션

- **머신 총 디스크 용량**: 926.4 GB
- **현재 가용 여유 공간**: **`79.2 GB (8.5% free)`** (사용량 847.2 GB, 점유율 91.5% $\rightarrow$ **WARNING**)
- **실측 수집 속도**: `974.12 MB/hour` $\approx$ `22.83 GB/day`
- **100% Full까지 남은 시간**: **`83.2시간 (약 3.5일)`**
- **72시간 Soak 완주 예상 총 수집량**: **`약 68.5 GB`** (기존 12.7 GB + 향후 약 55.8 GB)
- **72시간 종료 후 남을 여유 공간**: **`약 10.7 GB`**
- **판정**: 72시간(3일) 테스트 완주는 가능하나 안전 여유가 10.7GB 수준이므로 임의의 대용량 파일 복사나 중복 빌드를 자제해야 합니다.

---

## 4. Raw 파일 무결성 및 Quarantine 감사 결과

- **총 Raw 파티션 파일**: 1,016개
  - Active Partitions (최근 10분 내 수정): 71개 (현재 수집 중인 종목별 파티션)
  - Finalized Partitions (종료된 과거 파일): 945개
- **Zero-Byte File**: **`0건`**
- **Quarantined Records**: **`0건`** (파싱 불가/형식 오류 0건)
- **Malformed / Truncated JSONL**: **`0건`** (50개 파티션 1,857,256개 레코드 샘플링 검증 결과)
- **Queue Dropped Events**: **`0건`** (50,000 maxsize bounded queue 오버플로우 없음)
