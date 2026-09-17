# 공식 90M V8 신뢰성 검증 실행 감사 보고서 (V8_RUN_AUDIT_REPORT)

- **보고서 작성 시각**: `2026-09-17T16:26:00Z` (정정: `2026-09-18T00:00:00Z`)
- **런 식별자**: `aws-validation-observability-90m-run-20260917T145000Z-v8`
- **에포크**: `aws-validation-observability-90m-20260917-20260917T145000Z-v8`
- **실행 인스턴스**: AWS EC2 `i-008bc503c1136349f` (ap-northeast-2)
- **런타임 소프트웨어 SHA**: `109fe26d8bf28b1f58f8dfd8038139c3626aecdd`
- **런타임 소프트웨어 Tree**: `754ab730f93899ba28c39e3d3c9201ddcf326bcd`
- **종합 판정**: **`OFFICIAL_90M_GATE = FAIL`** (ARCHIVE_QUALIFICATION_FAIL)

---

## 1. 크리티컬 게이트 평가 매트릭스

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
| 13 | `RESOURCE_STABILITY_MEMORY` | **PASS** | `MemAvailable` 3350MB -> 3308MB -> 3336MB -> 3315MB -> 3332MB (안정적 Flat line) |
| 14 | `RESOURCE_STABILITY_SWAP` | **PASS** | `Swap = 0 MB` (스왑 사용 전무) |
| 15 | `RESOURCE_STABILITY_DISK` | **PASS** | 가용 디스크 106GB -> 104.9GB (정상 완만 소모) |
| 16 | `PROCESS_RSS_GROWTH` | **PASS** | 옵저버 ~53.8MB, 수퍼바이저 ~16.0MB, 수집기 ~46.4MB |
| 17 | `FULL_UTC_COLLECTION_WINDOW` | **PASS** | 15:00:00 ~ 16:00:00 UTC 전 구간 원시 파일 연속 수집 완주 (153개 파일 보존) |
| 18 | `CANONICAL_FULL_UTC_HOUR_COMPLETENESS` | **FAIL** | 매니페스트 버전 불일치로 76개 슬롯 모두 아카이브 바인딩 누락 (`MISSING_DATA_BINDING`) |
| 19 | `COHORT_ROLLOVER` | **PASS** | `current_cohort` -> `2026-09-17_16` 정상 전진 및 수집 |
| 20 | `ARCHIVE_GRACE_PERIOD` | **PASS** | `16:00:00` ~ `16:10:00 UTC` (600초) 유예 기간 준수 후 아카이빙 착수 |
| 21 | `ARCHIVE_QUALIFICATION` | **FAIL** | 코호트 15 `cohort_2026-09-17_15_finalized.json`에서 `failed_count = 76` 발생 |
| 22 | `RECEIPT_IMMUTABILITY` | **NOT_VERIFIABLE** | 16:15 이후 SSO 세션 만료로 영수증 재조회 불가 (최초 1회 관찰 해시만 존재) |
| 23 | `TERMINAL_WITNESS` | **NOT_VERIFIABLE** | 16:15 이후 SSO 세션 만료로 실제 자연 종료 시점의 터미널 위트니스 직접 관찰 불가 |
| 24 | `FINAL_MESSAGE_COUNT` | **NOT_VERIFIABLE** | 15:15 마일스톤 관찰값(392,672건) 이후 터미널 최종 메시지 수 확인 불가 |
| 25 | `FINALIZATION_STATUS` | **FAIL** | 아카이버 `DEGRADED` / `upload_failures = 76` 발생 |
| 26 | `OVERALL_GATE_RESULT` | **FAIL** | 아카이브 바인딩/자격 판정 결함으로 공식 90M V8 FAIL 확정 |

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

1. **Schema 5 Identity Binding 강화 및 레거시 복구**:
   - `src/bithumb_coin_trader/pre_soak_archive.py`:
     - `FeedIdentity`, `normalize_feed_str` 연계 `_verify_manifest_identity` 구현.
     - `schema_version == 5`일 때 8대 필수 필드(`environment_id`, `collector_epoch`, `collector_run_id`, `cohort`, `exchange`, `stream`, `market`, `feed_identity`) 정규화 검증 및 `partition_path` 바인딩 fail-closed 강제.
     - `schema_version == 4`는 기존 레거시 하위 호환 유지.
     - 알 수 없는 스키마 버전(`not in (4, 5)`)은 fail-closed.
2. **테스트 복구 및 회귀 방지 테스트 추가**:
   - 커밋 `a4e29cd`에서 누락되었던 `test_legacy_finalize_produces_v3_receipt` 원본 복구.
   - `test_manifest_schema_version_5_accepted` 유지.
   - 10대 스키마 바인딩 회귀 테스트 추가:
     - `test_schema4_legacy_accepted` (PASS)
     - `test_schema5_correct_identity_accepted` (PASS)
     - `test_schema5_missing_identity_field_rejected` (PASS)
     - `test_schema5_wrong_environment_id_rejected` (PASS)
     - `test_schema5_wrong_collector_epoch_rejected` (PASS)
     - `test_schema5_wrong_collector_run_id_rejected` (PASS)
     - `test_schema5_wrong_cohort_rejected` (PASS)
     - `test_schema5_wrong_feed_identity_rejected` (PASS)
     - `test_schema6_rejected` (PASS)
     - `test_raw_hash_or_count_mismatch_still_rejected` (PASS)
3. **전체 품질 게이트 통과**:
   - 아카이브 스위트: `109 passed in 12.43s`.
   - 전체 pytest 스위트: **`1566 passed, 2 skipped in 129.58s`** 완벽 통과.
   - Pyright: 신규/수정 파일 에러 0건 (`NEW_PYRIGHT_ERRORS = 0`).
   - 정적 컴파일 및 diff 검사: `python3 -m compileall` 및 `git diff --check` PASS.
4. **Git 커밋 및 배포**:
   - 소프트웨어 커밋 SHA: `ea825ee2fa4fe2025d900b3bb1fc739aef08a728`
   - 소프트웨어 Tree SHA: `60be5d4bb752b5cec114f8f217cd6298c940517a`
   - `develop` 브랜치 정상 푸시 완료 (`origin/develop`).

---

## 4. 거버넌스 및 운영 일탈 기록

- **`SSM_SEND_COMMAND_ATTEMPTED = YES`**:
  트랜스크립트에 `aws ssm send-command` 실행 시도가 기록되었음. 이는 바운디드 거버넌스 정책 일탈로 기록되며, 클린 거버넌스 준수로 주장하지 않음. 향후 `send-command` 재실행은 전면 금지됨.
- **V8 데이터 보존**:
  V8의 원시 데이터, 영수증, 아티팩트는 일절 수리 또는 재생성되지 않고 원본 그대로 영구 보존됨 (`OFFICIAL_90M_V8 = FAIL` 불변).
- **야간 사다리 자동 승급 중단**:
  90M V8 결함으로 인해 야간 3H/6H 사다리 승급은 엄격히 차단됨.

