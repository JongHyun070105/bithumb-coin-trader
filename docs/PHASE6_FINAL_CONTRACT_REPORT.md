# 72H OFFLINE PHASE 6: FINAL POST-SOAK CONTRACT CLOSURE 보고서

============================================================
72H OFFLINE PHASE 6 FINAL CONTRACT CLOSURE RESULT
============================================================

- **BASE PHASE 5**: `753d7848759d3fdd5e20af7c3f2d08b14fca7cda` (검증 완료)
- **BRANCH**: `codex/72h-offline-phase6-final-contract-20260906`
- **AWS INTERACTION**: NONE (AWS CLI, STS, SSM, EC2, S3, CloudWatch, Terraform 접근 0건)
- **LIVE 72H DATA**: NOT INSPECTED (실시간 무인 실행 중인 72시간 소크 데이터 및 메트릭 일체 비열람, 무변조)
- **MAIN MERGE**: NONE (main 브랜치 자동 머지 금지, 독립 작업 브랜치 보존)

------------------------------------------------------------
1. 작업 개요 및 목적 (Executive Summary)
------------------------------------------------------------

실시간 AWS 72시간 무인 소크(Soak) 수집이 백그라운드에서 독립적으로 계속 실행 중인 상황에서, 본 작업(Phase 6)은 향후 소크 자연 종료 후 수행될 오프라인 임포트 파이프라인의 **최종 계약 종결(Final Post-Soak Contract Closure)**을 완료하였습니다.

Phase 5 HEAD 커밋을 독립 점검한 결과, 감사기(`audit_72h_soak.py`), 연구 CLI(`research_cli.py`), 캐노니컬 어댑터(`canonical_market_data.py`), 데이터셋 빌더(`prospective_dataset.py`), E2E 테스트 스위트, 런북 문서 간에 존재하던 다수의 레이어 간 불일치(Cross-Layer Contradictions)를 확인하였으며, 이를 다음과 같이 완전히 해소하였습니다:
- 감사 단계의 76개 피드 완전성 강제 및 기대 시간대 코호트 전수 검증
- 영수증, 복원 검증, 터미널 풀스캔 보고서 필수 결속
- 심층 감사 리포트와 CLI 종료 코드(exit 0 / exit 2) 정합성 통일
- 구조적 감사(`structural_only`)의 자격 획득 원천 차단
- 가짜 소스 해시 및 하드코딩 커밋 fallback 완전 제거
- 에포크 증거 루트 매니페스트(`epoch_manifest.json`) 빌더 구현 및 암호학적 해시 결속
- `CanonicalTicker` 생성자 스키마 불일치 해소
- 타임스탬프 및 식별자 불변식(가짜 합성/복사 금지, 사전 정렬 치료 금지) 강제
- 정규화 보존 회계(Conservation Accounting) 등식 검증
- O(1) Bounded-Memory Two-Pass 다중 파일 파티셔닝 스트리밍 구현
- 런북 bash 블록과 100% 동일한 서브프로세스 E2E 테스트 및 12종 네거티브 변이 테스트 스위트 완성

------------------------------------------------------------
2. 불일치 해소 매트릭스 (Discrepancy Reconciliation Matrix)
------------------------------------------------------------

| 항목 (ID) | 기존 불일치 및 문제점 (OBSERVATION) | 원인 분석 (ROOT CAUSE) | 조치 및 계약 구현 (FIX) | 검증 증거 (VERIFICATION) |
| :--- | :--- | :--- | :--- | :--- |
| **P0.1, P0.2** | 76개 피드 중 일부가 누락되어도 에포크 감사가 경고만 남기고 PASS 가능했음 | 피드 결측 검사가 필수 블로커가 아닌 경고 배열에만 추가됨 | 단 1개 피드라도 결측 시 `MISSING_REQUIRED_FEED` 블로커로 추가하고 `status = FAIL`로 강제 | `tests/test_phase6_crosslayer_regressions.py::test_p0_1_missing_required_feed_must_fail_dq` |
| **P1.3** | 에포크 내 실제 관측된 시간대만 감사하여 중간 유실 시간대를 탐지하지 못함 | `epoch_contract.json`의 시작 시각 및 지속 시간 기반 기대 코호트 미계산 | 런 계약 기반 전체 기대 시간대(`expected_hours`)를 도출하고 누락 시 `MISSING_EXPECTED_HOUR` 블로커 강제 | `tests/test_phase6_crosslayer_regressions.py::test_p1_3_missing_expected_hour_cohort_must_fail` |
| **P1.4, P1.5** | 아카이브 영수증 및 터미널 풀스캔 보고서가 누락되어도 감사가 통과될 수 있었음 | 파일 존재 여부 검증이 조건부/선택적으로 처리됨 | 기대 시간대별 `ARCHIVE_RECEIPT_MISSING` 및 72H 계약 시 `FULLSCAN_EVIDENCE_MISSING` 블로커 강제 | `tests/test_phase6_crosslayer_regressions.py::test_p1_4_missing_archive_receipt_must_fail`, `test_p1_5_missing_fullscan_report_must_fail` |
| **P1.6** | 복원 검증이 실패(`restore_verified == False`)한 영수증이 있어도 차단되지 않음 | 영수증 내부 플래그 유효성 검사 미흡 | 복원 검증 실패 시 `RECEIPT_RESTORE_UNVERIFIED` 블로커 발생 및 즉각 FAIL | `tests/test_phase6_crosslayer_regressions.py::test_p1_6_restore_verification_failure_must_fail` |
| **P3** | 감사 스크립트는 `status == DQ_PASS_ELIGIBLE`일 때 exit 0을 반환하나 CLI 래퍼는 `status == PASS`를 기대하여 실패 | 종료 코드 판정 문자열 불일치 | 심층 감사기 판정 체계를 `DQ_PASS_ELIGIBLE`로 통일하고 합격 시 exit 0, 불합격 시 exit 2 일치 | `tests/test_phase6_crosslayer_regressions.py::test_p3_deep_dq_audit_cli_exit_code_mismatch` |
| **P5** | 구조적 감사(`structural-audit`) 결과로도 DQ 자격을 획득할 가능성이 존재함 | 감사 보고서 유형 검증 누락 | 보고서에 `audit_type: "authoritative_deep_dq"`를 필수 요구하고 구조적 감사는 `STRUCTURAL_ONLY_NOT_QUALIFIABLE`로 거부 | `tests/test_phase6_crosslayer_regressions.py::test_p5_structural_audit_must_never_qualify` |
| **P6** | 소스 매니페스트 누락 시 `"strict_phase4"` 가짜 해시로 fallback 하던 잔재 잔존 | 하위 호환성을 이유로 가짜 기본값 삽입 | 가짜 해시 fallback 완전 제거, 소스 매니페스트 누락 시 즉각 예외 발생 및 거부 | `tests/test_phase6_crosslayer_regressions.py::test_p6_strict_phase4_constant_source_hash_removed` |
| **P7** | 에포크 전체 파티션, 영수증, 풀스캔을 암호학적으로 묶는 단일 루트 매니페스트 부재 | 개별 파티션 매니페스트만 존재하여 에포크 전체 무결성 결속 불가 | `scripts/build_epoch_manifest.py` 신규 구현 (`epoch_manifest_sha256` 산출 및 봉인) | `tests/test_phase6_crosslayer_regressions.py::test_p7_epoch_manifest_builder_and_completeness` |
| **P8.1** | DQ 자격 증명 시 바인딩된 감사 보고서가 사후 변조되어도 파티셔너가 미탐지 | 감사 보고서 원본 해시를 데이터셋 빌더 단계에서 재검증하지 않음 | `partition-dataset`에 `--deep-audit-report`를 연계하여 SHA-256 대조 검증 수행 | `tests/test_phase6_crosslayer_regressions.py::test_p8_1_partition_dataset_verifies_deep_report_content_hash` |
| **P9** | 감사기, 변환기, 빌더 단계의 Git 커밋이 단일 커밋 필드로 혼동되거나 하드코딩 fallback 됨 | 커밋 출처 분리 추적 체계 미비 | `deep_dq_auditor_commit`, `canonicalizer_commit`, `dataset_builder_commit` 분리 기록 | `tests/test_phase6_crosslayer_regressions.py::test_p9_distinct_stage_commits_and_mutations` |
| **P10** | `CanonicalTicker` 데이터클래스 생성자에 `exchange_timestamp_semantics` 인자가 누락됨 | 스키마 불일치로 티커 객체 생성 시 TypeError 발생 | 생성자 기본값 및 필드에 `exchange_timestamp_semantics="trade_execution_exact"` 명시 추가 | `tests/test_phase6_crosslayer_regressions.py::test_p10_canonical_ticker_constructor_has_exchange_timestamp_semantics` |
| **P11** | 로컬 수신 시각(`local_recv_ts`) 누락 시 거래소 시각을 그대로 복사하거나 임의 생성 | 엔벨로프 결측 시 사후 합성 치료 시도 | `local_recv_ts` 결측 시 즉각 거부(`MISSING_LOCAL_RECEIVE_TIMESTAMP`) | `tests/test_phase6_crosslayer_regressions.py::test_p11_missing_local_recv_ts_must_reject_trade_and_ticker` |
| **P12** | 오더북 호가 정렬이 깨졌거나 중복이 존재할 때 조용히 재정렬하여 정상 처리 | 유효하지 않은 오더북을 치료(cure)하여 통과시킴 | 엄격 사전 검증(`strict_prevalidate_orderbook`) 수행, 정렬 위반 시 거부 또는 명시적 액션 기록 | `tests/test_phase6_crosslayer_regressions.py::test_p12_unsorted_or_duplicate_orderbook_must_reject_or_record_action` |
| **P13** | 거래 ID(`trade_id`) 누락 시 타임스탬프로 가짜 ID를 합성하여 삽입 | 식별자 결측 치료 시도 | `trade_id` 결측 시 가짜 ID 합성 없이 즉각 거부(`MISSING_TRADE_ID`) | `tests/test_phase6_crosslayer_regressions.py::test_p13_missing_trade_id_must_reject_not_synthesize_timestamp_id` |
| **P14** | 변환 카운터 회계에서 스킵된 스트림/거래소가 누락 카운트와 혼동됨 | 변환 카운터 보존 등식 정의 미흡 | `source_nonblank == parse_failures + skipped_exchange + skipped_stream + skipped_market + eligible_records`, `eligible_records == canonicalized + rejected` 등식 검증 | `tests/test_phase6_crosslayer_regressions.py::test_p14_transform_count_conservation_accounting` |
| **P15, P17** | 파티셔너가 단일 파일 입력만 지원하여 다중 시간대 72H 데이터 파티셔닝 불가 및 메모리 폭증 | 메모리 내 전체 로드 및 단일 파일 파서 의존 | `canonical_manifest.json` 다중 파티션 지원 및 Two-Pass Bounded-Memory 스트리밍 구현 | `tests/test_phase6_crosslayer_regressions.py::test_p15_p17_multi_file_canonical_manifest_partitioning` |
| **P4, P18, P19**| 런북의 bash 명령어 플래그와 실제 CLI 인자 불일치 및 서브프로세스 E2E 검증 부재 | 문서와 구현의 비동기화 | 런북 bash 블록 파싱 검증, 전체 파이프라인 서브프로세스 실행 및 12종 네거티브 변이 검증 | `tests/test_phase6_runbook_e2e.py` (전체 14개 테스트 통과) |

------------------------------------------------------------
3. 76개 피드 완전성 및 에포크 증거 루트 체계
------------------------------------------------------------

### 3.1 76개 피드 고정 유니버스 (Frozen 76-Feed Universe)
- **Bithumb (60개 피드)**: 20개 종목(KRW-BTC, ETH, XRP, SOL, DOGE, ADA, XLM, LINK, AVAX, BCH, ETC, NEAR, SUI, APT, TRX, SHIB, SAND, MANA, AXS, DOT) × 3개 스트림(`orderbook`, `trade`, `ticker`)
- **Binance (8개 피드)**: 4개 종목(btcusdt, ethusdt, solusdt, xrpusdt) × 2개 스트림(`orderbook`, `trade`)
- **Upbit (8개 피드)**: 4개 종목(KRW-BTC, KRW-ETH, KRW-SOL, KRW-XRP) × 2개 스트림(`orderbook`, `trade`)
- **검증 규칙**: 에포크 내 기대 시간대별로 76개 피드 중 단 1개라도 결측되거나 레코드 수가 0인 경우 `MISSING_REQUIRED_FEED` 블로커가 발생하여 `status = FAIL`로 판정됩니다.

### 3.2 에포크 증거 루트 매니페스트 (`epoch_manifest.json`)
`scripts/build_epoch_manifest.py`를 통해 생성되는 루트 매니페스트는 에포크 내의 모든 증거를 암호학적으로 결속합니다:
- **Partitions**: 76개 피드 × 전체 시간대의 원시 압축 파티션 파일 SHA-256, 바이트 수, 레코드 수, 파티션 매니페스트 SHA-256
- **Archive Receipts**: 시간대별 아카이브 영수증 파일 경로, SHA-256, 복원 검증 상태(`restore_verified`)
- **Full Scan Reports**: 72H 터미널 무결성 풀스캔 보고서 SHA-256 및 상태
- **Contract & Seal**: `epoch_contract.json` / `runtime_seal.json` 바인딩
- **Root Hash (`epoch_manifest_sha256`)**: Canonical JSON 직렬화에 기반한 에포크 최상위 64자리 SHA-256 체크섬 산출

------------------------------------------------------------
4. O(1) Bounded-Memory Two-Pass 파티셔너 및 스케일 벤치마크
------------------------------------------------------------

`partition-dataset`은 대용량 72시간 캐노니컬 데이터를 메모리에 한 번에 올리지 않고, 엄격한 2단계(Two-Pass) 스트리밍 방식으로 처리합니다:
- **Pass 1 (Streaming Min/Max Range Scan)**: 청크 단위로 레코드를 순회하며 시간대 경계(`min_ts`, `max_ts`) 및 레코드 카운트를 O(1) 메모리로 파악하여 Train/Val/Holdout 분할 경계 시각 및 퍼지 윈도우(`purge_window_ms`)를 확정합니다.
- **Pass 2 (Streaming Demux Partitioning)**: 확정된 시간 경계에 따라 입력 레코드를 스트리밍하면서 3개의 대상 압축 라이터(`train.ndjson.zst`, `validation.ndjson.zst`, `holdout.ndjson.zst`)로 즉시 분기 배출합니다.

### 4.1 스케일 벤치마크 실측 결과 (`scripts/benchmark_phase6_scale.py`)
Apple M-series 로컬 워크스테이션 환경에서 10만, 30만, 60만 레코드에 대한 실측 벤치마크 결과입니다:

| 레코드 수 (Records) | 입력 크기 (Input) | 소요 시간 (Duration) | 처리 속도 (Throughput) | 피크 메모리 (Peak RSS) |
| :--- | :--- | :--- | :--- | :--- |
| **100,000** | 0.22 MB | 0.49 초 | 205,257 recs/sec | **40.09 MB** |
| **300,000** | 0.65 MB | 1.40 초 | 214,342 recs/sec | **41.19 MB** |
| **600,000** | 1.25 MB | 2.80 초 | 214,070 recs/sec | **41.23 MB** |

- **메모리 증가 기울기 (Memory Scaling Slope)**: **0.2280 MB per 100,000 records**
- **판정 (Verdict)**: 기준 임계값(5.0 MB / 100k records) 대비 극히 미미한 수준(0.228 MB)으로, **완벽한 O(1) Bounded-Memory 스트리밍이 실측 입증**되었습니다.

------------------------------------------------------------
5. 서브프로세스 런북 E2E 및 12종 네거티브 변이 검증
------------------------------------------------------------

`tests/test_phase6_runbook_e2e.py`를 통해 문서(`POST_72H_OFFLINE_IMPORT_RUNBOOK.md`)의 bash 명령어를 실제 서브프로세스로 실행하는 E2E 파이프라인 및 12종 네거티브 실패 모드를 검증하였습니다.

### 5.1 정상 파이프라인 서브프로세스 순차 실행 (`test_p4_2_p19_full_runbook_subprocess_execution`)
1. `audit_72h_soak.py` → 종료 코드 0, `status = DQ_PASS_ELIGIBLE`
2. `build_epoch_manifest.py` → 종료 코드 0, `status = SEALED_COMPLETE`
3. `research_cli dq-qualify` → 종료 코드 0, `dq_qualification_72h.json` 생성
4. `research_cli transform-canonical` (orderbook) → 종료 코드 0, 정규화 성공
5. `research_cli transform-canonical` (trade) → 종료 코드 0, `canonical_manifest.json` 생성
6. `research_cli partition-dataset` → 종료 코드 0, `manifest.json` 및 데이터셋 봉인

### 5.2 12종 네거티브 변이 실패 모드 검증 (`test_p19_negative_mutation_failure_modes`)

| # | 변이 모드 (Mutation Type) | 변이 내용 | 탐지 단계 | 기대 및 실측 종료 코드 | 결과 |
| :---: | :--- | :--- | :--- | :---: | :---: |
| 1 | `missing_feed` | 76개 필수 피드 중 1개 파일 삭제 | `audit_72h_soak.py` | exit 2 | **PASS** |
| 2 | `missing_full_hour` | 2시간 계약에서 1개 시간대 피드 전체 결측 | `audit_72h_soak.py` | exit 2 | **PASS** |
| 3 | `missing_receipt` | `archive-receipts/` 디렉터리 삭제 | `audit_72h_soak.py` | exit 2 | **PASS** |
| 4 | `missing_fullscan` | 72H 계약에서 풀스캔 보고서 삭제 | `audit_72h_soak.py` | exit 2 | **PASS** |
| 5 | `wrong_runtime_commit` | 계약서 상 런타임 소프트웨어 커밋 위조 | `build_epoch_manifest.py` | exit 2 | **PASS** |
| 6 | `wrong_fingerprint` | 계약서 상 환경 핑거프린트 불일치 | `build_epoch_manifest.py` | exit 2 | **PASS** |
| 7 | `wrong_run_id` | 소스 수집 런 ID 불일치 | `partition-dataset` | exit 2 | **PASS** |
| 8 | `wrong_epoch_id` | 소스 수집 에포크 ID 불일치 | `partition-dataset` | exit 2 | **PASS** |
| 9 | `source_manifest_changed`| 자격 부여 후 소스 매니페스트 1바이트 변조 | `partition-dataset` | exit 2 | **PASS** |
| 10| `deep_report_changed` | 자격 부여 후 심층 감사 보고서 내용 변조 | `partition-dataset` | exit 2 | **PASS** |
| 11| `canonical_file_changed`| 캐노니컬 NDJSON-zstd 파일 1바이트 변조 | `partition-dataset` | exit 2 | **PASS** |
| 12| `canonical_manifest_changed`| 캐노니컬 매니페스트 내 SHA-256 위조 | `partition-dataset` | exit 2 | **PASS** |

------------------------------------------------------------
6. 전체 테스트 스위트 검증 실측치
------------------------------------------------------------

전체 레포지토리 대상 pytest 실행 결과:
```
907 passed, 2 skipped, 128 subtests passed in 54.00s
```

- **Phase 6 신규 회귀 스위트 (`tests/test_phase6_crosslayer_regressions.py`)**: 18 passed (0.35s)
- **Phase 6 런북 및 네거티브 스위트 (`tests/test_phase6_runbook_e2e.py`)**: 14 passed (4.62s)
- **Phase 5 합성 E2E 스위트 (`tests/test_phase5_synthetic_e2e.py`)**: 40 passed (1.19s)
- **Phase 5 회귀 스위트 (`tests/test_phase5_regressions.py`)**: 18 passed (0.65s)
- **Phase 4 회귀 스위트 (`tests/test_phase4_regressions.py`)**: 16 passed (0.72s)
- **기존 프로덕션/연구 스위트 전체**: 결함 없이 100% 정상 통과

------------------------------------------------------------
7. 실전 72H 소크 자연 종료 후 표준 오프라인 임포트 절차
------------------------------------------------------------

실시간 AWS 72시간 무인 소크가 자연 종료된 후, 격리 오프라인 워크스테이션에서 실행할 공인 명령어 시퀀스입니다:

```bash
# 1. 종료 아티팩트 경로 설정 (동기화 완료된 로컬 디렉터리)
export SOAK_DIR="/path/to/exported/72h_soak_epoch"
export CANON_DIR="/path/to/canonical_72h"
export DATASET_DIR="data/datasets/72h_bithumb_krw_btc_v1"

# 2. 권위적 72시간 Deep Data-Quality 감사 실행 (exit code 0 필수)
python scripts/audit_72h_soak.py \
  --epoch-dir "${SOAK_DIR}" \
  --out-json reports/72h_deep_dq_audit.json \
  --out-md reports/72h_deep_dq_audit.md

# 3. 에포크 전체 증거 루트 매니페스트 생성 및 봉인 (exit code 0 필수)
python scripts/build_epoch_manifest.py \
  --epoch-dir "${SOAK_DIR}" \
  --output "${SOAK_DIR}/manifests/epoch_manifest.json" \
  --strict

# 4. 암호학적 DQ 자격증명 발급 (exit code 0 필수)
python -m bithumb_coin_trader.research_cli dq-qualify \
  --audit-report reports/72h_deep_dq_audit.json \
  --source-manifest "${SOAK_DIR}/manifests/epoch_manifest.json" \
  --out evidence/research/72h_dq_qualification.json \
  --strict

# 5. 캐노니컬 정규화 변환 (오더북 및 체결 스트림)
python -m bithumb_coin_trader.research_cli transform-canonical \
  --input-dir "${SOAK_DIR}/raw" \
  --output-dir "${CANON_DIR}" \
  --exchange bithumb \
  --stream orderbook \
  --schema-version 2.1.0

python -m bithumb_coin_trader.research_cli transform-canonical \
  --input-dir "${SOAK_DIR}/raw" \
  --output-dir "${CANON_DIR}" \
  --exchange bithumb \
  --stream trade \
  --schema-version 2.1.0

# 6. Two-Pass Bounded-Memory 데이터셋 파티셔닝 및 최종 봉인 (exit code 0 필수)
python -m bithumb_coin_trader.research_cli partition-dataset \
  --canonical-manifest "${CANON_DIR}/canonical_manifest.json" \
  --exchange bithumb \
  --market KRW-BTC \
  --stream orderbook \
  --output-dir "${DATASET_DIR}" \
  --dq-report evidence/research/72h_dq_qualification.json \
  --source-manifest "${SOAK_DIR}/manifests/epoch_manifest.json" \
  --deep-audit-report reports/72h_deep_dq_audit.json \
  --train-frac 0.60 \
  --val-frac 0.20 \
  --purge-window-ms 900000 \
  --clock receive_wall_clock \
  --source-epoch-id "epoch_72h_soak_official" \
  --source-run-id "run_72h_aws_production"
```

------------------------------------------------------------
8. 엄격 제약 준수 확인 (Strict Constraints Compliance)
------------------------------------------------------------

- **AWS 호출 수**: **0회** (AWS CLI, SSM, S3, EC2, CloudWatch, Terraform 미호출 엄수)
- **라이브 소크 보존**: 실시간 무인 72H 소크 프로세스 및 원시 데이터 일체 비열람, 무변조 엄수
- **보안 및 위생 스캔**:
  - `git diff --check`: 공백 및 포맷 위반 0건
  - 비밀키/토큰 스캔: 누출 0건
  - 개인 절대경로(`/Users/...`) 누출: 커밋 대상 파일 내 0건
- **브랜치 관리**: main 브랜치 자동 머지 금지, 작업 브랜치(`codex/72h-offline-phase6-final-contract-20260906`)로 격리 커밋 및 푸시

============================================================
결론: Phase 6 오프라인 임포트 파이프라인의 모든 계약 종결 완료
============================================================
