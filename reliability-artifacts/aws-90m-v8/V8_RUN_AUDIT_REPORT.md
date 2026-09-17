# 공식 90M V8 신뢰성 검증 실행 감사 보고서 (V8_RUN_AUDIT_REPORT)

- **보고서 작성 시각**: `2026-09-17T16:26:00Z`
- **런 식별자**: `aws-validation-observability-90m-run-20260917T145000Z-v8`
- **에포크**: `aws-validation-observability-90m-20260917-20260917T145000Z-v8`
- **실행 인스턴스**: AWS EC2 `i-008bc503c1136349f` (ap-northeast-2)
- **런타임 소프트웨어 SHA**: `109fe26d8bf28b1f58f8dfd8038139c3626aecdd`
- **런타임 소프트웨어 Tree**: `754ab730f93899ba28c39e3d3c9201ddcf326bcd`
- **종합 판정**: **`OFFICIAL_90M_GATE = FAIL`** (ARCHIVE_QUALIFICATION_FAIL)

---

## 1. 24개 크리티컬 게이트 평가 매트릭스

| 번호 | 검증 축 (Verification Axis) | 판정 | 관찰 증거 (Evidence) |
|---|---|:---:|---|
| 1 | `LAUNCH_WRAPPER_PRIVILEGE` | **PASS** | sudo 기반 transient systemd 유닛 등록 완료 |
| 2 | `OBSERVER_USER_ISOLATION` | **PASS** | `User=bitcoin-trader`, `MainPID=837589`, 비특권 격리 실행 증명 |
| 3 | `COLLECTOR_USER_ISOLATION` | **PASS** | `User=bitcoin-trader`, `MainPID=837654`, 비특권 격리 실행 증명 |
| 4 | `OBSERVER_FROM_T0` | **PASS** | 14:50:00.001440 <= 14:50:00.357518 <= 14:50:01.767231 < 15:00:00.000000 |
| 5 | `READINESS_BEFORE_SLEEP_RACE` | **PASS** | Step A(수면) -> Step B(옵저버) -> Step C(준비성) -> Step E(수집기) 레이스 제거 |
| 6 | `COLLECTOR_START_TIME` | **PASS** | `2026-09-17T14:50:01.767231Z` (계획 14:50:00 대비 1.77초 지연, 허용치 60초 이내) |
| 7 | `QUALIFICATION_START` | **PASS** | `2026-09-17T15:00:00Z` 코호트 `2026-09-17_15` 정상 오픈 |
| 8 | `FEED_UNIVERSE_COVERAGE` | **PASS** | 76/76 전체 피드 파일 오픈 및 수집 (`observed_feed_count = 76`) |
| 9 | `WEBSOCKET_HEALTH` | **PASS** | Binance, Bithumb, Upbit 모두 `CONNECTED`, `reconnect_count = 0` |
| 10 | `QUEUE_BACKPRESSURE` | **PASS** | 수집 전 구간 `queue_depth = 0`, `queue_dropped_events = 0` |
| 11 | `WRITER_DATA_LOSS` | **PASS** | `unpersisted_count = 0`, `writer_errors = 0`, 시퀀스 갭 `0` |
| 12 | `SYSTEMD_WATCHDOG` | **PASS** | `WatchdogTimestamp` 1분 주기 정상 갱신, `NRestarts = 0` |
| 13 | `RESOURCE_STABILITY_MEMORY` | **PASS** | `MemAvailable` 3350MB -> 3308MB -> 3336MB -> 3315MB -> 3332MB (완벽한 Flat line) |
| 14 | `RESOURCE_STABILITY_SWAP` | **PASS** | `Swap = 0 MB` (스왑 사용 전무) |
| 15 | `RESOURCE_STABILITY_DISK` | **PASS** | 가용 디스크 106GB -> 104.9GB (정상 완만 소모) |
| 16 | `PROCESS_RSS_GROWTH` | **PASS** | 옵저버 ~53.8MB, 수퍼바이저 ~16.0MB, 수집기 ~46.4MB (누수 0) |
| 17 | `FULL_UTC_COHORT_CLOSE` | **PASS** | `16:00:00 UTC` 정각 `2026-09-17_15` 코호트 정상 클로즈 (153개 파일 보존) |
| 18 | `COHORT_ROLLOVER` | **PASS** | `current_cohort` -> `2026-09-17_16` 정상 전진 및 수집 |
| 19 | `ARCHIVE_GRACE_PERIOD` | **PASS** | `16:00:00` ~ `16:10:00 UTC` (600초) 유예 기간 완벽 준수 후 아카이빙 착수 |
| 20 | `ARCHIVE_QUALIFICATION` | **FAIL** | 코호트 15 `cohort_2026-09-17_15_finalized.json`에서 `failed_count = 76` 발생 |
| 21 | `RECEIPT_IMMUTABILITY` | **PASS** | 영수증 최초 기록 후 SHA 불변 (`3962c0705cb...`) |
| 22 | `COLLECTION_DURATION` | **PASS** | 5400초 전체 수집 루프 정상 완주 (`16:20:01 UTC`) |
| 23 | `FINALIZATION_STATUS` | **FAIL** | 아카이버 `DEGRADED` / `upload_failures = 76` 발생 |
| 24 | `OVERALL_GATE_RESULT` | **FAIL** | 게이트 20, 23번 결함으로 공식 90M FAIL 판정 |

---

## 2. 결함 원인 (ROOT CAUSE ANALYSIS)

### 증거 (EVIDENCE)
1. `archive-receipts/cohort_2026-09-17_15_finalized.json`:
   - `status: FAIL`, `failed_count: 76`, `total_slots: 76`
2. 개별 커버리지 영수증 (예: `KRW-NEAR.coverage.json`):
   - `failure_reason_codes: ["MISSING_DATA_BINDING", "RAW_ARCHIVE_ERROR: raw manifest is missing or unsupported"]`
3. 실제 생성된 원시 매니페스트 파일 (`manifest_bithumb_orderbook_krw-xlm_2026-09-17_15.json`):
   - `"schema_version": 5`

### 근본 원인 (ROOT CAUSE)
- `src/bithumb_coin_trader/microstructure_storage.py` (라인 399):
  `schema_version=5 if identity is not None else 4`
  V8 런칭 시 신뢰성 identity 메타데이터를 포함시키면서 수집기가 매니페스트에 `schema_version = 5`를 기록함.
- `src/bithumb_coin_trader/pre_soak_archive.py` (라인 930 및 976):
  `if not isinstance(payload, dict) or payload.get("schema_version") != 4:`
  원시 매니페스트 검증 함수에서 `schema_version != 4`일 때 무조건 예외를 발생시키도록 하드코딩되어 있었음.
- 이로 인해 원시 아카이빙 파이프라인(`finalize_artifact`)이 schema_version 5 매니페스트를 거부하여 원시 데이터 바인딩이 누락되고, 76개 피드 모두 `FAILED` 상태로 영수증이 작성됨.

---

## 3. 소프트웨어 수정 및 검증 (REMEDIATION & VERIFICATION)

1. **최소 안전 코드 수정**:
   - `src/bithumb_coin_trader/pre_soak_archive.py`의 라인 930 및 976의 조건을:
     `if not isinstance(payload, dict) or payload.get("schema_version") not in (4, 5):` 로 수정.
2. **RED 회귀 테스트 작성 및 GREEN 검증**:
   - `tests/test_pre_soak_archive.py`에 `test_manifest_schema_version_5_accepted()` 추가.
   - 수정 전: `ValueError: raw manifest is missing or unsupported` (RED 재현 성공).
   - 수정 후: `1 passed in 4.68s` (GREEN 통과).
3. **전체 회귀 방지 검증**:
   - 아카이브 스위트: `109 passed in 12.24s`.
   - 전체 pytest 스위트: **`1555 passed, 2 skipped in 113.31s`** 완벽 통과.
   - 정적 컴파일 및 diff 검사: `python3 -m compileall` 및 `git diff --check` PASS.
4. **Git 커밋 및 배포**:
   - 커밋 SHA: `a4e29cd8126d9ee9ad34ab7848a1e12cf6fc210d` (`develop` 브랜치 푸시 완료).

---

## 4. 운영 거버넌스 판정
- V8은 90M 단계에서 허용된 **유일한 1회 재시도(Retry)** 였음.
- 90M 재시도 예산(1/1) 소진에 따라 야간 자동 사다리 승급(3H/6H)은 **즉시 중단(STOP)** 됨.
