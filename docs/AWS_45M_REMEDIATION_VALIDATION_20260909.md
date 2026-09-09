# AWS 45-Minute Post-Remediation Unattended Validation Report (2026-09-09)

## 1. Executive Summary

Post-72H Runtime Remediation V1(후보 커밋 `4b1c7d409c6fa998b733293873bbd1d5ba064897`)의 실제 AWS/systemd 환경 동작을 검증하기 위한 1회 한정 45분 무인(Unattended) 런타임 스모크 검증을 수행하였습니다.

- **45M PROCESS:** **PASS** (런타임 수퍼바이저, 2700초 자연 수집, 120초 finalization 예산 내 완주, 152개 매니페스트 플러시, systemd 정상 종료 100% 통과)
- **45M ARCHIVE:** **FAIL** (04시 코호트 76개 피드 압축 성공했으나, S3 `HeadObject` 403 Forbidden 차단으로 영수증 `state: FAILED` 귀결)
- **45M EVIDENCE CONTRACT:** **FAIL** (아카이브 S3 권한 차단으로 인해 76개 적격 영수증 및 152개 모달 풀스캔 리포트 미충족)
- **AWS 45M OVERALL:** **FAIL** (Fail-Closed 원칙에 따라 필수 게이트 미충족으로 전체 판정 FAIL)
- **READY FOR POST-45M REVIEW:** **YES**
- **NEW 72H STARTED:** **NO** (절대 착수 금지 원칙 준수)

---

## 2. Validation Metadata & Provenance

| 항목 | 값 |
|---|---|
| **Candidate Code Commit** | `4b1c7d409c6fa998b733293873bbd1d5ba064897` |
| **Validation Branch** | `codex/aws-45m-remediation-validation-20260909` |
| **Runtime Seal Path** | `infra/aws/seals/aws-45m-validation-20260909.runtime.json` |
| **Runtime Seal SHA-256** | `fa2fcc28f43e2ad3245d671e9a3fc02b2b11e637d831d3cce687ceec619405c9` |
| **Canonical Runtime Fingerprint** | `9f5ee627ab6db7e6a4154a0fb830719493bf066ea9cfd2b9d16ae20ee18cc544` |
| **Validation Epoch** | `aws-45m-val-20260909-0440` |
| **Validation Run ID** | `aws-45m-val-run-20260909T044000Z` |
| **Target Instance ID** | `i-008bc503c1136349f` (t3.medium, ap-northeast-2a) |
| **Systemd Unit** | `bitcoin-trader-short-smoke-aws-45m-val-run-20260909T044000Z.service` |
| **Service Execution User** | `bitcoin-trader` (privileged transient launch pattern) |

---

## 3. Authoritative Timing & Lifecycles

| 이벤트 | 타임스탬프 (UTC) | 경과 시간 / 비고 |
|---|---|---|
| **Launch Command Executed** | `2026-09-09T04:40:02Z` | systemd-run 기동 |
| **Supervisor Started** | `2026-09-09T04:40:05.159791+00:00` | 권위적 기동 시각 |
| **Collector Process Started** | `2026-09-09T04:40:05.304721+00:00` | PID: 415560 |
| **Hour Boundary (05:00 UTC)** | `2026-09-09T05:00:01+00:00` | 04시 코호트 수집 마감, 05시 파티션 로테이션 |
| **Archive Grace Expiry (600s)** | `2026-09-09T05:10:00+00:00` | 스케줄러 자율 아카이브 오케스트레이션 착수 |
| **Collection Duration Endpoint** | `2026-09-09T05:25:05.355743+00:00` | **정확히 2700.05초** (45분 정규 수집 완주) |
| **Phase: FINALIZING Transition** | `2026-09-09T05:25:05.355743+00:00` | 버퍼 플러시 및 152개 매니페스트 플러시 시작 |
| **Phase: COMPLETE Transition** | `2026-09-09T05:25:21.495352+00:00` | 최종 플러시 완료 (Finalization 소요: **16.14초**) |
| **Supervisor Terminal Exit** | `2026-09-09T05:25:36.753508+00:00` | 총 소요: **2731.59초** (하드 천장 2820초 미만) |
| **Systemd Deactivation** | `2026-09-09T05:25:36Z` | `status=0/SUCCESS`, `Deactivated successfully` |

- **불변 조건 검증:**
  `collection duration (2700s) < supervisor elapsed (2731.59s) < supervisor hard ceiling (2820s) < systemd RuntimeMaxSec (2880s)` 성립.
- **종료 방식:** 인위적 SIGTERM/SIGKILL/stop 일절 없이, 완벽한 자율 라이프사이클(`COLLECTING -> FINALIZING -> COMPLETE`)에 의해 종료됨.

---

## 4. Cohort Analysis & Invariant Evaluation

### 1) 수학적 코호트 도출
- **수집 시작:** 04:40:05 UTC (04시 코호트 `2026-09-09_04`)
- **수집 종료:** 05:25:05 UTC (05시 코호트 `2026-09-09_05`)
- **접촉된 RAW 코호트 (Touched RAW cohorts):** 2개 (`2026-09-09_04`, `2026-09-09_05`)
- **아카이브 적격 코호트 (Archive-eligible cohorts):** 1개 (`2026-09-09_04`)
  - 05:00:00 UTC에 04시 코호트 마감 + 600초 유예 만료(05:10:00 UTC) 충족.
  - 05시 코호트는 수집 종료(05:25:05 UTC) 시점에 아직 시간 마감(06:00 UTC)에 도달하지 않았으므로 아카이브 대상이 아님.

### 2) 코호트별 세부 현황

| 코호트 | RAW 파일 수 | 압축 파일 수 | 영수증 수 | 영수증 상태 | 풀스캔 입력 수 | 풀스캔 리포트 |
|---|---|---|---|---|---|---|
| `2026-09-09_04` | 76 | 76 | 76 | FAILED (403 HeadObject) | 0 (미생성) | INCOMPLETE |
| `2026-09-09_05` | 76 | 0 (미대상) | 0 (미대상) | N/A (미대상) | N/A (미대상) | N/A |
| **합계** | **152** | **76** | **76** | **0 Qualifying (76 FAILED)** | **0 Qualifying** | **FAIL** |

---

## 5. Runtime Health & Data Quality Metrics

- **총 수신 메시지:** **677,668건**
  - Binance: 395,342건 (181.27 MB)
  - Bithumb: 211,813건 (282.48 MB)
  - Upbit: 70,513건 (174.21 MB)
- **총 수집 데이터 용량:** **637,959,298 바이트 (~638 MB)**
- **무결성 지표:**
  - `writer_errors`: **0**
  - `queue_dropped_events`: **0**
  - `reconnect_count`: **0**
  - `unpersisted_event_count`: **0**
  - `trade_duplicates`: **0**
  - `trade_sequence_gaps`: **0**
  - `malformed_quarantined`: **0**
  - `fatal_writer_error_type`: **null**
  - `writer_fail_closed`: **false**
- **파일시스템 검증:**
  - RAW 0바이트 파일: **0개**
  - 압축 0바이트 파일: **0개**
  - RAW 총 파일 수: **152개** (76개 피드 × 2개 시간 코호트 100% 일치)
  - 매니페스트 수: **152개**
  - 파일 소유권: 100% `bitcoin-trader:bitcoin-trader` (비소유 아티팩트 0개)
  - 디스크 공간: 200 GiB 중 74 GiB 사용 (37%, 127 GiB 잔여)

---

## 6. Root Cause Analysis (RCA): Archive S3 HeadObject 403 Forbidden

### 현상 (Observation)
- 05:10:00 UTC 자율 아카이브 시도 시, 04시 코호트 76개 피드 전체가 `zstd` 압축에 성공하였으나 S3 원격 전송/검증 단계에서 다음과 같은 실패가 발생하여 76개 영수증이 모두 `"state": "FAILED"`로 기록됨:
  `"failure_reason": "ClientError: An error occurred (403) when calling the HeadObject operation: Forbidden"`
  `"failure_stage": "COMPRESSED_VERIFIED"`

### 증거 (Evidence)
1. **게스트 영수증 원본:**
   `remote_key: market-data/temporary/aws-45m-val-20260909-0440/2026-09-09/binance/orderbook/binance_orderbook_btcusdt_2026-09-09_04.jsonl.zst`
2. **EC2 인스턴스 프로파일 IAM 정책 경계 (`collector-permissions-boundary-20260905-candidate.json`):**
   ```json
   {
     "Sid": "EpochArchiveObjects",
     "Effect": "Allow",
     "Action": [
       "s3:AbortMultipartUpload",
       "s3:GetObject",
       "s3:ListMultipartUploadParts",
       "s3:PutObject"
     ],
     "Resource": [
       "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/canonical/aws-72h-soak-20260905-8017b83e/*",
       "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433/market-data/temporary/aws-72h-soak-20260905-8017b83e/*"
     ]
   }
   ```

### 원인 규명 (Root Cause)
- EC2 인스턴스에 할당된 IAM Permissions Boundary가 과거 72H 에포크인 `aws-72h-soak-20260905-8017b83e`의 S3 접두사(prefix)로만 하드코딩되어 있음.
- 따라서 신규 에포크인 `aws-45m-val-20260909-0440` 접두사에 대한 S3 데이터 플레인 권한(`HeadObject`/`GetObject`)이 IAM 레벨에서 차단(403)됨.
- AWS SAFETY 원칙에 따라 임의의 IAM 정책 변경 및 권한 확장을 금지하였으므로, 아카이버는 Fail-Closed 원칙에 따라 런타임 패닉 없이 모든 영수증을 `FAILED`로 안전하게 확정함.
- **런타임 코드 무결성:** 본 문제는 런타임 소스 코드의 결함이 아니며, 인프라의 에포크별 IAM S3 리소스 경계 제약에 기인함.

---

## 7. Hardened Auditor Execution Results

`scripts/audit_72h_soak.py`의 `validate_archive_evidence_coverage` 감사 결과:
- **총 발견 영수증:** 76개
- **적격 터미널 영수증 (Qualifying receipts):** **0개**
- **부적격 영수증 (RECEIPT_INVALID_STATE):** **76개** (`state=FAILED`)
- **풀스캔 리포트 (FULLSCAN_COHORT_COVERAGE_INCOMPLETE):** **미충족** (`2026-09-09_04`)
- **영수증 오염 (Contamination):** 0건 (타 피드 혼입, 중복 피드 없음)
- **감사 판정:** **FAIL** (엄격한 152개 모달 풀스캔 및 76개 적격 영수증 미충족)

---

## 8. Final Gate Verdicts

```
============================================================
AWS 45M REMEDIATION VALIDATION
============================================================

CANDIDATE:
4b1c7d409c6fa998b733293873bbd1d5ba064897

VALIDATION HEAD:
ddde885205741997fc5b0c4fd1e5bd9c8eb34d17

RUNTIME FINGERPRINT:
9f5ee627ab6db7e6a4154a0fb830719493bf066ea9cfd2b9d16ae20ee18cc544

ACTUAL START:
2026-09-09T04:40:05.159791+00:00

ACTUAL COMPLETION:
2026-09-09T05:25:36.753508+00:00

COLLECTION DURATION:
2700.0 seconds

FINALIZATION DURATION:
16.14 seconds

SUPERVISOR ELAPSED:
2731.59 seconds

------------------------------------------------------------
PROCESS
------------------------------------------------------------

SUPERVISOR STATUS:
PASS

COLLECTOR EXIT:
0

FORCED TIMEOUT:
FALSE

EXTERNAL SIGNAL:
NONE (null)

FINAL MANIFEST:
FLUSH OBSERVED (152 manifests)

FINAL METRICS:
VALID (written_at 05:25:05 UTC, active_partition_files [])

SCHEDULER EXIT:
0 (clean)

SYSTEMD RESULT:
SUCCESS (Deactivated successfully)

45M PROCESS:
PASS

------------------------------------------------------------
COHORTS
------------------------------------------------------------

TOUCHED RAW COHORTS:
2026-09-09_04, 2026-09-09_05 (2 cohorts)

EXPECTED ARCHIVE COHORTS:
2026-09-09_04 (1 cohort)

ACTUAL ARCHIVE COHORTS:
2026-09-09_04 (1 cohort attempted, 76 FAILED)

------------------------------------------------------------
ARCHIVE EVIDENCE
------------------------------------------------------------

EXPECTED RECEIPTS:
76

ACTUAL QUALIFYING RECEIPTS:
0 (76 FAILED due to IAM S3 HeadObject 403)

PER COMPLETE COHORT:
76 (Expected) / 0 Qualifying

EXPECTED FULLSCAN INPUTS PER COHORT:
152 (76 RAW + 76 COMPRESSED)

RAW INPUTS:
76 files present (zero 0-byte)

COMPRESSED INPUTS:
76 files present (zero 0-byte)

RESTORE:
FAILED (S3 HeadObject 403)

45M ARCHIVE:
FAIL

45M EVIDENCE CONTRACT:
FAIL

------------------------------------------------------------
RUNTIME HEALTH
------------------------------------------------------------

WRITER ERRORS:
0

QUEUE DROPS:
0

UNPERSISTED:
0

ZERO-BYTE FILES:
0 (RAW: 0, Compressed: 0)

DISK:
HEALTHY (74 GiB / 200 GiB, 37% used)

RESTARTS:
0

CONTAMINATION:
NONE (zero foreign run IDs, zero foreign feeds)

------------------------------------------------------------
OVERALL
------------------------------------------------------------

AWS 45M OVERALL:
FAIL

READY FOR POST-45M REVIEW:
YES

NEW 72H STARTED:
NO

------------------------------------------------------------
SAFETY
------------------------------------------------------------

ROOT USED:
NO (bitcoin-trader service user used)

IAM CHANGES:
NONE

TERRAFORM:
NONE

SECURITY GROUP CHANGES:
NONE

PRIVATE API:
DISABLED

TRADING:
DISABLED

OLD EVIDENCE MUTATED:
NO (historical 72H soak directory unchanged)

CLEANUP:
FALSE

MAIN MERGED:
NO
============================================================
```
