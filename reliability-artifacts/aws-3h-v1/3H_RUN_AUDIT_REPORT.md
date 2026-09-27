# 공식 AWS Fresh 3h 신뢰성 검증 최종 실행 감사 보고서 (3H_RUN_AUDIT_REPORT)

- **보고서 작성 시각**: `2026-09-18 18:05:00 KST` (`2026-09-18T09:05:00Z UTC`)
- **런 식별자 (Run ID)**: `aws-validation-observability-3h-run-20260918T055000Z-v1`
- **에포크 (Epoch)**: `aws-validation-observability-3h-20260918-20260918T055000Z-v1`
- **런타임 소프트웨어 커밋**: `e752c3da084d4e27ec7b81b1518c54bc112dba54` (origin/develop 최신)
- **런타임 소프트웨어 트리**: `f91bc6abcb4b6efae652481405e4ce2893c6238a`
- **아티팩트 봉인 커밋**: `9a1e2cc` (`chore(reliability): seal fresh 3h validation launch artifacts (aws-3h-v1)`)
- **최종 공식 판정**: **`OFFICIAL_3H_V1_RESULT = PASS`** (전 게이트 만점 합격)

---

## 1. 종합 게이트 판정 매트릭스

| 평가 항목 | 판정 | 상세 실증 증거 (Evidence) |
| :--- | :---: | :--- |
| **COLLECTION_PHASE** | **PASS** | 10,800초(3시간 = 180분) 무중단 완수, 총 2,247,583건(2.38 GB) 수집, 0 큐, 0 드롭, 0 에러 |
| **SUPERVISOR_STATUS** | **PASS** | `overall_status = PASS`, 수집기(PID 891851), 스케줄러(PID 891852), 퍼블리셔(PID 903936) 전원 exit code 0 |
| **RESOURCE_STABILITY** | **PASS** | 3시간 동안 옵저버 RSS 53.4 MB, 수집기 65.7 MB 완벽한 수평선 유지 (`NO_OBSERVED_MEMORY_LEAK_DURING_3H = PASS`), 잔여 디스크 91.28 GB |
| **T0_SEQUENCING** | **PASS** | `OBSERVER_START(14:50:00.006) <= OBSERVER_READY(14:50:00.387) <= COLLECTOR_START(14:50:00.937) < QUAL_START(15:00:00.000)` 완벽 준수 (`OBSERVER_FROM_T0 = PASS`) |
| **GOVERNANCE_ISOLATION** | **PASS** | 옵저버, 수집기, 스케줄러, 슈퍼바이저 전원 `User=bitcoin-trader` 권한으로 실행 (루트 쉘/임의 권한 상승 배제) |
| **ARCHIVE_QUALIFICATION_C06** | **PASS** | 적격 코호트 1(`2026-09-18_06`) 76/76 전 피드 S3 아카이브 완결 (`failed_count = 0`), 커버리지 바인딩 100% |
| **ARCHIVE_QUALIFICATION_C07** | **PASS** | 적격 코호트 2(`2026-09-18_07`) 76/76 전 피드 S3 아카이브 완결 (`failed_count = 0`), 커버리지 바인딩 100% |
| **RECEIPT_IMMUTABILITY_C06** | **PASS** | 1차 관측(16:12 KST) 및 2차 관측(16:30 KST) 간 17분 31초 시차 SHA256 체크섬 100% 일치 |
| **RECEIPT_IMMUTABILITY_C07** | **PASS** | 1차 관측(17:12 KST) 및 2차 관측(17:51 KST) 간 39분 30초 시차 SHA256 체크섬 100% 일치 |
| **OFFICIAL_3H_V1_RESULT** | **PASS** | **Fresh 3h 신뢰성 검증 게이트 최종 전수 만점 합격** |

---

## 2. 슈퍼바이저 터미널 실행 증거 (`result.json`)

```json
{
  "archive_scheduler_exit_code": 0,
  "archive_scheduler_pid": 891852,
  "archive_scheduler_started": true,
  "archive_scheduler_stopped_after_collector": true,
  "collection_duration_seconds": 10800.0,
  "collector_exit_code": 0,
  "collector_pid": 891851,
  "deadline_recomputed": false,
  "elapsed_seconds": 10820.952103,
  "ended_at": "2026-09-18T08:50:22.067055+00:00",
  "final_manifest_flush_observed": true,
  "final_metrics_valid": true,
  "finalization_timeout_seconds": 180.0,
  "forced_timeout": false,
  "full_duration_satisfied": true,
  "hard_ceiling_seconds": 10980.0,
  "overall_status": "PASS",
  "publisher_exit_code": 0,
  "publisher_pid": 903936,
  "publisher_started": true,
  "publisher_stopped_after_collector": false,
  "received_signal": null,
  "run_id": "aws-validation-observability-3h-run-20260918T055000Z-v1",
  "schema_version": 2,
  "started_at": "2026-09-18T05:50:01.114726+00:00",
  "supervisor_pid": 891849
}
```

---

## 3. 적격 코호트 아카이브 영수증 및 불변성 증명

### 1) 적격 코호트 #1 (`2026-09-18_06`, 15:00~16:00 KST)
- **영수증 본문**:
```json
{
  "status": "PASS",
  "cohort": "2026-09-18_06",
  "cohort_qualification": "QUALIFYING_FULL_HOUR",
  "total_slots": 76,
  "data_present_count": 76,
  "verified_zero_count": 0,
  "failed_count": 0,
  "failed_feeds": [],
  "finalized_at_utc": "2026-09-18T07:10:05.132509+00:00"
}
```
- **불변성 대조**:
  - 1차 관측 (`2026-09-18 16:12:47 KST`): `425ca123424dc7d170ee09ca9a5a61f9bf9357ee60e1c0a462e8464cb05ca507`
  - 2차 관측 (`2026-09-18 16:30:18 KST`): `425ca123424dc7d170ee09ca9a5a61f9bf9357ee60e1c0a462e8464cb05ca507`
  - 시차: 17분 31초 (1,051초) -> **`RECEIPT_IMMUTABILITY = PASS`**

### 2) 적격 코호트 #2 (`2026-09-18_07`, 16:00~17:00 KST)
- **영수증 본문**:
```json
{
  "status": "PASS",
  "cohort": "2026-09-18_07",
  "cohort_qualification": "QUALIFYING_FULL_HOUR",
  "total_slots": 76,
  "data_present_count": 76,
  "verified_zero_count": 0,
  "failed_count": 0,
  "failed_feeds": [],
  "finalized_at_utc": "2026-09-18T08:10:14.226953+00:00"
}
```
- **불변성 대조**:
  - 1차 관측 (`2026-09-18 17:12:23 KST`): `e1a1f71690c79df43cfac23783d88abc47cbbc6c6528341a9facc22927a804bd`
  - 2차 관측 (`2026-09-18 17:51:53 KST`): `e1a1f71690c79df43cfac23783d88abc47cbbc6c6528341a9facc22927a804bd`
  - 시차: 39분 30초 (2,370초) -> **`RECEIPT_IMMUTABILITY = PASS`**

---

## 4. 최종 수집 메트릭 증거 (`collector_metrics.json`)

- **최종 총 메시지 수**: **2,247,583건** (약 **2,381.37 MB = 2.38 GB**)
  - **Binance**: 1,223,730건 (호가 401,155, 체결 822,575 / 드롭 0, 에러 0, 단절 0)
  - **Bithumb**: 730,329건 (호가 689,934, 체결 23,153, 현재가 17,242 / 드롭 0, 에러 0, 단절 0)
  - **Upbit**: 293,524건 (호가 247,993, 체결 45,531 / 드롭 0, 에러 0, 단절 0)
- **큐 및 라이터 상태**:
  - `final_queue_size`: **0**
  - `final_unpersisted_count`: **0**
  - `queue_dropped_events`: **0**
  - `writer_errors`: **0**
  - `fatal_writer_error_type`: `null`
- **가동률 및 세션 무결성**:
  - 3개 거래소 모두 단 1회의 재연결/단절 없이 10,820.25초간 **100.0% 무중단 연결 유지** (`reconnect_count = 0`, `disconnect_count = 0`)

---

## 5. S3 아카이브 적재 검증

- **S3 임시 접두사**: `s3://bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-validation-observability-3h-20260918-20260918T055000Z-v1/`
- **총 적재 객체 수**: **448개**
  - 워밍업 코호트 (`2026-09-18_05`): 76개 피드 데이터 + 커버리지
  - 적격 코호트 1 (`2026-09-18_06`): 76개 피드 데이터 + 커버리지
  - 적격 코호트 2 (`2026-09-18_07`): 76개 피드 데이터 + 커버리지
  - 옵저버 분 단위 witness 무결 적재 완료

---

## 6. 글로벌 런칭 회계

- **상한**: `NEW GLOBAL AWS LAUNCH CAP = 6`
- **소진 내역**:
  - #1: V8 (90m, 실패)
  - #2: V10 (90m, 사전 중단)
  - #3: V12 (90m, 실패)
  - #4: V13 (90m, **PASS**)
  - #5: Fresh 3h-v1 (3h, **PASS**)
  - **누적 소진: 5 / 6**
- **잔여 런칭 예산**: **1회** (오직 Fresh 6h #6에만 전량 할당)
- **하드 데드라인**: **2026-09-19 02:00 KST** (`2026-09-18T17:00:00Z UTC`)
  - 현재 시각: **2026-09-18 18:05 KST**
  - 데드라인까지 잔여 시간: **7시간 55분**
