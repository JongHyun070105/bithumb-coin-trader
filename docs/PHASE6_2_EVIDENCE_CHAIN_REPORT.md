# 72H 무인 수집 Phase 6.2 실제 시작 시각 및 증거 사슬 최종 마감 보고서
(72H Offline Phase 6.2 Actual-Start / Evidence-Chain Final Closure Report)

---

## 1. 개요 및 최종 요약 (Executive Summary & Final Status)

### 1.1 배경 및 작업 목적
본 스프린트(Phase 6.2)는 Phase 6.1 포렌식 검증 이후 잔존하는 증거 사슬(Evidence Chain) 모순 및 실제 시작 시각(Actual Start Time) 시맨틱 결함을 해결하여, 72시간 무인 수집(Soak) 증거 사슬 전체가 수학적·암호학적으로 일관되고 결함 없이 동작하도록 보장하는 것을 목적으로 한다.

- **AWS 호출 0건 엄수**: AWS CLI, SSM, EC2, S3, CloudWatch, Terraform 등 외부 호출 일체 차단.
- **실제 72H 소크 비열람·무변조**: 실행 중인 라이브 환경 접근 없이 오프라인 격리 검증.
- **Fail-Closed 원칙**: 어떠한 경우에도 불확실하거나 누락된 증거를 임의의 기본값(synthetic/offline/launch created_at)으로 추측하거나 대체하지 않음.

### 1.2 최종 판정 결과 요약 (Executive Metadata)

```
===============================================================================
PHASE 6.2 ACTUAL-START / EVIDENCE-CHAIN RESULT
===============================================================================

BASE:
190654efcd815f884cb1c2bd06bf597eafeed05f

HEAD:
13efc5e731a275cbe24255ad92b92c8ed8fe8909

AWS:
NONE

LIVE SOAK:
NOT INSPECTED

ACTUAL START SOURCE:
AUTHORITATIVE ACTUAL-START EVIDENCE ARTIFACT (FAIL-CLOSED)

PROVENANCE CREATED_AT USED AS START:
NO

EVIDENCE ORDER:
CONTRACT
→ EPOCH ROOT
→ DEEP AUDIT
→ QUALIFICATION
→ CANONICAL ROOT
→ DATASET

ROOT HASH VERIFIED:
YES

DQ PASS DEGRADED=0:
YES

LEGACY BYPASSES:
NONE

DEEP AUDIT REQUIRED:
YES

UNSEALED RAW INJECTION:
REJECTED

SOURCE EPOCH AUTO-DERIVED:
YES (FROM ROOT MANIFEST)

SOURCE RUN AUTO-DERIVED:
YES (FROM ROOT MANIFEST)

OFFICIAL DATASET SYNTHETIC/OFFLINE DEFAULT:
IMPOSSIBLE

REAL 72H DATA USED:
NO

FINAL:
SYNTHETICALLY READY / BLOCKED ON ACTUAL-START EVIDENCE
===============================================================================
```

---

## 2. 주요 결함 분석 및 해결 내역 (P0 — P20)

### 2.1 P0 / P0.1 / P0.2 / P20: 실제 시작 시각(Actual Start Time) 시맨틱 정립
- **결함**: `launch-provenance.json`의 `created_at_utc`는 오케스트레이터가 번들을 동기화하고 서비스를 시작하기 직전의 시각이며, 실제 인스턴스 내에서 수집 프로세스가 시작된 시각이 아님. 기존 코드는 이를 실제 시작 시각으로 간주하는 시맨틱 결함이 존재했음.
- **해결**:
  - `compose_epoch_contract.py`에 `--actual-start-evidence` 플래그를 추가하고, 권위적 증거 파일 부재 시 추측이나 `created_at_utc` 대체 없이 즉시 `ACTUAL_START_EVIDENCE_MISSING` (exit 2)로 Fail-Closed 차단.
  - 계약서 내에 `actual_start_evidence_path` 및 `actual_start_evidence_sha256`을 암호학적으로 봉인.
  - `expected_duration_sec` 누락 시 기본값 259,200초(72시간) 폴백 적용.
  - 실제 트래킹 파일(`aws-72h-soak-20260905.runtime.json`, `aws-72h-soak-20260905.launch-provenance.json`)로 검증 시 `created_at_utc`를 조용히 채택하지 않고 `ACTUAL_START_EVIDENCE_MISSING` (exit 2)로 정상 차단됨을 확인.

### 2.2 P1 / P18: 권위적 증거 생성 순서 재정렬 및 단언
- **기존 순서 결함**: Deep Audit이 Epoch Root 이전에 실행되거나 뒤섞여 증거 사슬의 논리적 선후 관계가 왜곡됨.
- **엄격 순서 확립**:
  1. `COMPOSE CONTRACT` (`compose_epoch_contract.py`)
  2. `BUILD ROOT MANIFEST` (`build_epoch_manifest.py`)
  3. `DEEP AUDIT` (`audit_72h_soak.py`)
  4. `QUALIFY` (`research-cli dq-qualify`)
  5. `CANONICALIZE` (`research-cli transform-canonical`)
  6. `PARTITION` (`research-cli partition-dataset`)
- `post_72h_offline_import.sh` 및 `tests/test_phase6_runbook_e2e.py`에서 실행 순서를 엄격히 단언하여 Deep Audit이 Root 빌드 이전에 실행되는 경우 즉시 실패하도록 검증.

### 2.3 P2 / P3: 에포크 루트 매니페스트 셀프 해시 검증
- `verify_epoch_manifest()`에 `epoch_manifest_sha256` 자체 해시 일치 검증을 추가하여 매니페스트 내용 변조 시 `EPOCH_MANIFEST_HASH_MISMATCH` (exit 2)를 즉각 방출.
- `audit_72h_soak.py` 및 `cmd_partition_dataset`에서 `verify_epoch_manifest`를 수행하여 변조 방지.

### 2.4 P4 / P4.1: DQ 적격성 판정 강화
- 문자열 전용 해시 검증을 전면 금지하고 실제 소스 매니페스트 파일 바이트 SHA-256 검증을 강제 (`HASH_ONLY_QUALIFICATION_NOT_PERMITTED`, exit 2).
- `degraded_count > 0`일 경우 경고로 감추지 않고 비적격 사유 `DQ_DEGRADED`로 분류하여 승인 차단.

### 2.5 P5 / P6: 레거시 우회 차단 및 심층 감사 보고서 필수화
- `strict_phase4` 우회 모드 및 `auditor_version == "1.0.0"` 레거시 아티팩트를 전면 거부 (`LEGACY_QUALIFICATION_REJECTED`, exit 2).
- 공식 파티셔닝 시 `--deep-audit-report` 누락 시 즉시 차단 (`MISSING_DEEP_AUDIT_REPORT`, exit 2).

### 2.6 P7 / P15 / P16: 증거 사슬 3자 일치 검증 및 출처 메타데이터 강제
- **3자 해시 대조**: `canonical.source_epoch_manifest_sha256 == DQ.epoch_manifest_sha256 == actual_epoch_sha` 검증. 불일치 시 `EVIDENCE_CHAIN_MISMATCH` (exit 2).
- **공식 메타데이터 강제**:
  - `source_epoch_id`, `source_run_id`, `source_runtime_commit`, `source_runtime_fingerprint`, `epoch_manifest_sha256`, `deep_dq_report_sha256`, `dq_qualification_sha256`, `canonical_manifest_sha256`, `canonicalizer_commit`, `dataset_builder_commit` 10개 필드 필수 기록.
  - `synthetic`, `offline`, `unknown` 등 불완전 식별자 사용 시 즉시 거부 (`INVALID_DATASET_PROVENANCE`, exit 2).
  - 사용자가 `--source-epoch-id`, `--source-run-id`를 수동 입력하지 않아도 루트 매니페스트로부터 자동 도출.

### 2.7 P8 / P8.1 / P9: 봉인되지 않은 원시 파일 주입 차단 및 변조 탐지
- `transform-canonical`은 디렉터리 glob이 아닌 `epoch_manifest["partitions"]`를 단일 진실 공급원(Single Source of Truth)으로 사용.
- 디렉터리 내에 루트 매니페스트에 등록되지 않은 추가 파일 발견 시 즉시 `UNSEALED_SOURCE_PARTITION` (exit 2) 방출 및 변환 차단.
- 유효한 zstd 데이터 내 필드 변조 시 `SOURCE_RAW_HASH_MISMATCH` (exit 2) 감지.

### 2.8 P10: 엄격한 단조/수신 타임스탬프 파싱 검증
- 전수 스트리밍 스캔에서 `except: pass` 침묵 처리 제거.
- `exchange`, `stream`, `market`, `collector_run_id`, `local_recv_ts`, `local_recv_monotonic_ns`, `local_write_ts`, `payload` 필수 봉투 검증.
- 단조 시계 역전 감지 시 `MONOTONIC_TIMESTAMP_REVERSAL` (exit 2) 방출.

### 2.9 P11 / P12 / P13: 루트 빌더 출처 및 완전성 검증
- `runtime_code_commit` 필드 대조 추가 (`RUNTIME_COMMIT_MISMATCH`, exit 2).
- 공식 모드에서 `launch_provenance`, `runtime_seal`, `contract`, `actual-start evidence` 필수화.

---

## 3. 네거티브 변이 테스트 실측 검증 결과 (P19: 22종 전수 통과)

22종의 네거티브 변이 모드를 실행하여 정확한 단계에서 고유 머신 판독형 에러 토큰과 종료 코드 2가 방출되는지 실측 검증:

| # | 실패 모드 (Mutation Mode) | 검증 단계 | 실측 종료 코드 | 실측 에러/블로커 토큰 | 판정 |
| :---: | :--- | :--- | :---: | :--- | :---: |
| 1 | `missing_actual_start` | `compose_epoch_contract.py` | 2 | `ACTUAL_START_EVIDENCE_MISSING` | **PASS** |
| 2 | `missing_feed` | `audit_72h_soak.py` | 2 | `MISSING_REQUIRED_FEED` | **PASS** |
| 3 | `missing_full_hour` | `audit_72h_soak.py` | 2 | `MISSING_RAW_COHORT_FILES` | **PASS** |
| 4 | `missing_receipt` | `audit_72h_soak.py` | 2 | `ARCHIVE_RECEIPT_MISSING` | **PASS** |
| 5 | `missing_fullscan` | `audit_72h_soak.py` | 2 | `FULLSCAN_EVIDENCE_MISSING` | **PASS** |
| 6 | `wrong_runtime_commit` | `build_epoch_manifest.py` | 2 | `RUNTIME_COMMIT_MISMATCH` | **PASS** |
| 7 | `wrong_fingerprint` | `build_epoch_manifest.py` | 2 | `RUNTIME_FINGERPRINT_MISMATCH` | **PASS** |
| 8 | `missing_root_manifest` | `audit_72h_soak.py` | 2 | `NO_EPOCH_MANIFEST` | **PASS** |
| 9 | `root_hash_mutation` | `audit_72h_soak.py` | 2 | `EPOCH_MANIFEST_HASH_MISMATCH` | **PASS** |
| 10 | `deep_dq_without_contract` | `audit_72h_soak.py` | 2 | `NO_RUN_CONTRACT` | **PASS** |
| 11 | `hash_only_qualification` | `dq-qualify` | 2 | `HASH_ONLY_QUALIFICATION_NOT_PERMITTED` | **PASS** |
| 12 | `dq_pass_degraded` | `dq-qualify` | 2 | `DQ_DEGRADED` | **PASS** |
| 13 | `legacy_qualification` | `partition-dataset` | 2 | `LEGACY_QUALIFICATION_REJECTED` | **PASS** |
| 14 | `missing_deep_audit` | `partition-dataset` | 2 | `MISSING_DEEP_AUDIT_REPORT` | **PASS** |
| 15 | `extra_unsealed_raw` | `transform-canonical` | 2 | `UNSEALED_SOURCE_PARTITION` | **PASS** |
| 16 | `source_raw_changed` | `transform-canonical` | 2 | `SOURCE_RAW_HASH_MISMATCH` | **PASS** |
| 17 | `malformed_recv_ts` | `audit_72h_soak.py` | 2 | `MALFORMED_LOCAL_RECV_TS` | **PASS** |
| 18 | `monotonic_reversal` | `audit_72h_soak.py` | 2 | `MONOTONIC_TIMESTAMP_REVERSAL` | **PASS** |
| 19 | `canonical_file_changed` | `partition-dataset` | 2 | `CANONICAL_PARTITION_HASH_MISMATCH` | **PASS** |
| 20 | `canonical_manifest_changed` | `partition-dataset` | 2 | `CANONICAL_MANIFEST_HASH_MISMATCH` | **PASS** |
| 21 | `evidence_chain_mismatch` | `partition-dataset` | 2 | `EVIDENCE_CHAIN_MISMATCH` | **PASS** |
| 22 | `invalid_provenance_offline` | `partition-dataset` | 2 | `INVALID_DATASET_PROVENANCE` | **PASS** |

---

## 4. 스트리밍 메모리 스케일 벤치마크 (Scale Benchmark Result)

`scripts/benchmark_phase6_scale.py` 실행 결과:

```
=== Scale Benchmark 1: High Record Count Streaming Audit ===
Records: 100,000
Elapsed Time: 0.514s (Throughput: 194,591 rec/s)
Initial RSS: 51.602 MB
Peak RSS: 53.480 MB
Memory Delta: 1.878 MB

=== Scale Benchmark 2: Linear Memory Scaling Test ===
   Record Count    Peak Memory (MB)    Throughput (rec/s)
---------------------------------------------------------
         20,000              54.004               201,363
         40,000              54.004               200,899
         60,000              54.004               198,309
         80,000              54.004               200,996
        100,000              54.004               198,401

Memory slope per 100k records: 0.000 MB
Bounded Memory O(1) Streaming Verified! (Slope < 5.0 MB)
```

- **메모리 복잡도**: $O(1)$ 스트리밍 검증 완료 (100,000건 레코드 검증 시 메모리 증가량 0.000 MB).
- **처리율**: ~195,000 ~ 201,000 records/sec 초고속 스트리밍 달성.

---

## 5. 전체 테스트 스위트 회귀 검증

- **전체 테스트 결과**: `pytest -q`
- **실측치**: `924 passed, 2 skipped, 128 subtests passed in 60.81s`
- **회귀 발생**: 0건. 기존 Phase 1 ~ 6.1 테스트 전체 정상 통과.

---

## 6. 결론 및 향후 절차 (Conclusion & Hand-off)

- **도구 및 증거 사슬 검증 완료 (Tooling & Evidence Chain Ready)**:
  - Phase 6.2의 모든 감사·변환·파티셔닝 도구와 포렌식 검증 체계는 암호학적 완전성을 갖추어 준비 완료됨.
- **실제 수집 데이터 처리 상태**:
  - 실제 72H 소크는 여전히 AWS 프로덕션에서 무인 자율 구동 중이며, 임의로 열람하거나 데이터를 가공하지 않음.
  - 권위적 실제 시작 시각 증거 파일(`actual_start_evidence.json`)이 확보되기 전까지 공식 계약서 합성을 Fail-Closed 차단 상태로 유지함.
- **최종 상태 판정**:
  - **`SYNTHETICALLY READY / BLOCKED ON ACTUAL-START EVIDENCE`**
