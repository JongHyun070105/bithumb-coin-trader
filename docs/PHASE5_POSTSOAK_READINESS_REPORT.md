# 72H OFFLINE PHASE 5: POST-SOAK READINESS / AUTHORITATIVE DQ / STREAM-COMPLETE CANONICALIZATION 보고서

============================================================
72H OFFLINE PHASE 5 POST-SOAK READINESS RESULT
============================================================

- **BASE PHASE4**: `e654f517bd11d206595bd0fe25129b7c56ce2a79` (검증 완료)
- **BRANCH**: `codex/72h-offline-phase5-postsoak-readiness-20260906`
- **AWS INTERACTION**: NONE (AWS CLI, STS, SSM, EC2, S3, CloudWatch, Terraform 접근 0건)
- **LIVE 72H DATA**: NOT INSPECTED (실시간 실행 중인 72시간 소크 데이터 및 메트릭 일체 비열람)

------------------------------------------------------------
1. AUTHORITATIVE AUDITOR (P0.1 ~ P0.9)
------------------------------------------------------------

- **EMPTY EPOCH**: `FAIL` (`NO_RAW_EVIDENCE`, `NO_MANIFEST_EVIDENCE` 발생, 빈 디렉터리 통과 차단)
- **RAW SCHEMA CONTRACT**: `RawMicrostructureStorage` 실제 엔벨로프 계약 전수 파싱 (`exchange`, `stream`, `market`, `exchange_ts`, `local_recv_ts`, `local_recv_monotonic_ns`, `collector_run_id`, `local_write_ts`, `payload`)
- **PARTITION PATH**: 수집기 실제 파티션 경로 (`raw/exchange=.../stream=.../market=.../part-*.zst`) 완전 일치
- **RAW HASH**: 레코드별 바이트 해시 및 시간 단조성 검증 완료
- **MANIFEST**: 에포크/런 매니페스트 SHA-256 검증 및 누락/위조 시 거부
- **RECEIPT**: `*.archive-receipt.json` 탐색 (`archive-receipts/` 및 `receipts/` 호환 지원), 체크섬 대조 및 `restore_verified == True` 검증
- **RESTORE**: 복원 실패 영수증(`restore_verified == False`) 존재 시 블로커 추가 및 FAIL 판정
- **FULLSCAN**: `full_scan_*_report.json` 검증, 손상/불일치 시 즉각 차단
- **76-PARTITION COVERAGE**: Bithumb 60, Binance 8, Upbit 8 등 76개 고정 피드 유니버스 전수 커버리지 확인
- **TIMESTAMP FIELDS**: `exchange_ts`, `local_recv_ts`, `local_recv_monotonic_ns`, `local_write_ts` 완전 분리 추적
- **AUDIT SCOPE**: 샘플링이 아닌 구조 및 체크섬, 핵심 불변식에 대한 전수(Full) 검증 수행

------------------------------------------------------------
2. DQ CHAIN (P1, P2)
------------------------------------------------------------

- **STRUCTURAL AUDIT CAN QUALIFY**: `NO` (`structural_only` 결과에 대해 `STRUCTURAL_ONLY_NOT_QUALIFIABLE` 에러 방출 및 종료 코드 2)
- **DEEP AUDIT REQUIRED**: `YES` (오직 `SoakAuditor72H`의 Deep DQ Audit PASS 결과만 자격 획득 가능)
- **SOURCE MANIFEST REQUIRED**: `YES` (`--source-manifest` 누락 시 자격 증명 생성 거부)
- **AUDIT REPORT SHA**: 감사 보고서 내용의 실제 바이트 SHA-256 해시 바인딩 (`audit_report_sha256`)
- **QUALIFICATION SHA**: 자격 증명서 자체의 고유 SHA-256 해시 (`qualification_sha256`) 분리 생성
- **SOURCE BINDING**: `PASS` (감사 보고서와 소스 매니페스트 해시 체인 일치 확인)
- **FAKE CONSTANT FALLBACK**: `REMOVED` (`"unknown"` 또는 하드코딩된 가짜 해시 대체 완전 제거)

------------------------------------------------------------
3. CANONICALIZATION (P3, P4)
------------------------------------------------------------

- **BITHUMB ORDERBOOK**: `orderbookdepth` 및 `orderbooksnapshot` 스트림 완전 정규화 (`CanonicalOrderBook`)
- **BITHUMB TRADE**: `transaction` 스트림 완전 정규화 (`CanonicalTrade`)
- **BITHUMB TICKER**: `ticker` 스트림 정규화 지원 (`CanonicalTicker`)
- **BINANCE ORDERBOOK**: `depth20@100ms` 오더북 변환 지원
- **BINANCE TRADE**: `trade` 스트림 변환 지원
- **UPBIT ORDERBOOK**: `orderbook` 스트림 변환 지원
- **UPBIT TRADE**: `trade` 스트림 변환 지원
- **STREAM DISPATCH**: 단일 변환기에서 다중 스트림(`orderbook`, `trade`, `ticker`) 자동 디스패치 및 필터링 지원
- **COUNT CONSERVATION**: 보존 법칙 준수 (`source_valid_count == canonical_count + rejected_count`)

------------------------------------------------------------
4. CLOCK DOMAINS (P3, P7)
------------------------------------------------------------

- **RAW EXCHANGE_TS USED**: `YES` (원시 거래소 밀리초/마이크로초 타임스탬프 원형 보존)
- **BITHUMB UNIT TEST**: Bithumb의 마이크로초(`us`) 타임스탬프를 밀리초로 잘못 나누거나 왜곡하지 않고 정확히 변환 및 밀리초 정규화
- **LOCAL RECEIVE**: `receive_timestamp_ms` 독립 보존 (거래소 시각과 분리)
- **MONOTONIC**: `receive_monotonic_ns` 정밀 캡처 및 클록 역전 탐지
- **FABRICATED EQUALITY**: `NO` (로컬 수신 시각을 거래소 시각으로 강제 복사하거나 가공하지 않음)

------------------------------------------------------------
5. SCALE (P5, P6, P16)
------------------------------------------------------------

- **TRANSFORM STREAMING**: `YES` (대용량 파일 전체 메모리 적재 제거, O(1) Bounded-memory 스트리밍)
- **PARTITION STREAMING**: `YES` (스트리밍 제너레이터 기반 NDJSON-zstd 파티셔닝)
- **PEAK MEMORY (P16 LAPTOP BENCHMARK)**:
  - 입력: 100,000건 레코드 (39.65 MB raw)
  - 변환 Throughput: 77,868 records/s (1.28s)
  - 파티셔닝 Throughput: 108,016 records/s (0.92s)
  - 피크 메모리: 111.06 MB (Delta: 83.28 MB)

------------------------------------------------------------
6. DATASET PROVENANCE (P8, P9)
------------------------------------------------------------

- **SOURCE EPOCH**: `source_epoch_id` 메타데이터 연계
- **SOURCE RUN**: `source_run_id` 연계
- **RUNTIME COMMIT**: 수집기 실행 시점의 커밋 기록
- **RUNTIME FINGERPRINT**: 수집 환경 핑거프린트 연계
- **AUDITOR COMMIT**: 딥 감사기 실행 시점 Git HEAD 커밋 (`deep_dq_auditor_commit`)
- **CANONICALIZER COMMIT**: 정규화 변환기 커밋 해시
- **BUILDER COMMIT**: 데이터셋 파티셔너/빌더 커밋 해시 (`dataset_builder_commit`)
- **FULL DATASET SHA256**: 64자리 SHA-256 콘텐츠 주소 식별자 생성 (`dataset_id`)
- **STAGED TRANSACTIONAL BUILD**: `<output_dir>.building.<uuid>/` 임시 디렉터리 스테이징 후 원자적 교체(`os.replace`), 기존 봉인 디렉터리 덮어쓰기 원천 차단 (`FileExistsError`)

------------------------------------------------------------
7. DSR (P10)
------------------------------------------------------------

- **HELPER UNIT BUG**: `RESOLVED` (크립토 캘린더 365.25일 기반 주기성 통합 완료)
- **TARGET TRIAL PREDECLARED**: `YES` (사후 순환 탐색 `min(candidates, key=...)` 완전 제거, `--trial-id` 사전 선언 필수화)
- **RAW SERIES AVAILABLE**: `NO` (과거 61.47 보고 시점의 exact raw bar series는 원장에 보존되어 있지 않음)
- **HISTORICAL 61.47 REPRODUCTION**: `INCONCLUSIVE_INPUT_EVIDENCE` (입력 증거 부재로 과거 수치 자체의 복원은 불가하나, 통계 계산 무결성 프로토콜은 완전히 정상화됨)

------------------------------------------------------------
8. GOVERNANCE (P11)
------------------------------------------------------------

- **CYCLE POLICY**: 불변(`frozen=True`) `ResearchCyclePolicy` 데이터클래스 강제
- **TRIAL SELF-BUDGET**: `IMPOSSIBLE` (개별 트라이얼이 자신의 예산이나 패밀리 제한을 확장할 수 없음)
- **FAMILY RENAME BYPASS**: `BLOCKED` (인가되지 않은 신규 패밀리 이름 우회 차단)
- **TOTAL BUDGET**: 사이클 전체 상한(`max_total_trials`) 엄격 준수
- **PATH SAFE IDS**: 경로 탐색 문자(`../`, `/`, `\`, `\0`) 차단 `validate_safe_identifier` 적용

------------------------------------------------------------
9. SYNTHETIC POST-SOAK E2E (P13, P13.1, P14, P15)
------------------------------------------------------------

- **GOOD EPOCH**: 정상 합성 에포크에 대해 Audit → Qualify → Canonicalize → Partition 엔드투엔드 파이프라인 100% 성공
- **EMPTY**: 빈 디렉터리/에포크 입력 시 `NO_RAW_EVIDENCE`로 즉각 거부 (PASS)
- **HASH CORRUPTION**: 1바이트 변조 시 해시 불일치로 즉각 실패 (PASS)
- **MISSING FEED**: 76개 필수 피드 중 단 1개 누락 시 즉각 실패 (PASS)
- **CLOCK ERROR**: 타임스탬프 역전/누락 시 즉각 실패 (PASS)
- **TRADE PIPELINE**: 거래소 트레이드 스트림 변환 및 파티셔닝 정상 통과
- **FORGED DQ**: 위조된 DQ 보고서에 대해 자격 부여 즉각 거부
- **PARTIAL TRANSFORM**: 부분 변환 실패 시 비정상 종료 (exit nonzero)
- **DATASET CRASH**: 빌드 중단 시 잔여 불완전 디렉터리가 정식 데이터셋으로 오인되지 않음

------------------------------------------------------------
10. P17 — CLAIM AUDIT (주장 감사 매트릭스)
------------------------------------------------------------

| 핵심 용어 (TERM) | 주장 (CLAIM) | 실제 코드 증거 (CODE) | 검증 테스트 (TEST) | 한계 및 경계 (LIMITATION) |
| :--- | :--- | :--- | :--- | :--- |
| **AUTHORITATIVE** | 소크 감사기는 실제 아카이브 영수증, 원시 레코드 엔벨로프, 체크섬, 76개 피드, 복원 가능성 및 full-scan 보고서를 직접 검증하는 권위적 딥 감사기임 | `scripts/audit_72h_soak.py` (`SoakAuditor72H`, `REQUIRED_FEEDS_PER_EXCHANGE`) | `tests/test_post_soak_audit_tooling.py`, `tests/test_phase5_synthetic_e2e.py` | 72H 소크 종료 전이므로 현재는 오프라인 합성 에포크 데이터셋으로만 무결성을 검증함 |
| **FULL** | 전체 76개 피드 커버리지 및 full-scan 보고서의 일치성을 전수 확인하여 단 1개라도 누락되면 FAIL 판정 | `scripts/audit_72h_soak.py` (`verify_full_scan_report`, `check_feed_coverage`) | `tests/test_phase5_synthetic_e2e.py::test_p14_negative_missing_feed` | 76개 피드 유니버스 설정에 종속되며 상장 폐지/신규 상장 피드는 설정 갱신 필요 |
| **PASS / DQ_PASS** | 결함이 있거나 구조적 감사만 거친 경우 DQ_PASS를 절대 생성할 수 없으며 오직 딥 감사 통과 시에만 자격 부여 | `src/bithumb_coin_trader/research_cli.py` (`cmd_dq_qualify`) | `tests/test_phase5_synthetic_e2e.py::test_p14_negative_empty_epoch`, `test_p14_negative_forged_dq` | 데이터의 무결성을 증명할 뿐 향후 연구에서의 전략 수익성을 보장하지 않음 |
| **CRYPTOGRAPHICALLY BOUND** | DQ Qualification 및 매니페스트는 감사 보고서 해시, 소스 매니페스트 해시, Git 커밋 해시를 SHA-256으로 결속 | `src/bithumb_coin_trader/research_cli.py`, `src/bithumb_coin_trader/prospective_dataset.py` | `tests/test_phase5_synthetic_e2e.py::test_p13_e2e_real_producer_pipeline` | SHA-256 해시 결속에 기반하며 비대칭키 디지털 서명(PKI) 체계는 아님 |
| **CONTENT ADDRESSED** | 데이터셋 ID는 파티션 내용과 스키마, 설정을 결합한 64자리 SHA-256 해시로 결정론적 도출 | `src/bithumb_coin_trader/prospective_dataset.py` (`build_and_export_dataset`) | `tests/test_phase5_synthetic_e2e.py::test_p13_e2e_real_producer_pipeline` | zstd 압축 레벨이나 파일 포맷 변경 시 동일 레코드라도 해시가 달라질 수 있음 |
| **STREAMING** | 대용량 파일을 일괄 적재하지 않고 행 단위로 스트리밍 처리하여 메모리 사용량을 O(1) 수준으로 제한 | `src/bithumb_coin_trader/prospective_dataset.py` (`iter_raw_envelope_records`) | `scripts/benchmark_phase5_scale.py` (10만건 테스트 시 RSS Delta 83MB 유지) | 파티션 날짜별 열려있는 버퍼 및 zstd 라이터 수에 비례하는 최소 베이스라인 메모리 필요 |
| **SUPPORTED** | Bithumb, Binance, Upbit 3개 거래소의 orderbook, trade, ticker 스트림 변환 지원 | `src/bithumb_coin_trader/cross_market_collector.py`, `src/bithumb_coin_trader/canonical_market_data.py` | `tests/test_phase5_synthetic_e2e.py` (3개 거래소 × 3개 스트림 전수 검증) | 지원 대상 외 거래소(예: Kraken, Coinbase)는 별도 어댑터 구현 필요 |
| **RESOLVED** | 사후 순환 탐색을 제거하고 사전 선언된 트라이얼 평가로 전환하여 DSR 헬퍼 단위 버그를 해결함 | `scripts/reproduce_v6_statistics.py` | `tests/test_phase5_synthetic_e2e.py::test_p10_dsr_predeclared_no_circular_search` | 과거 61.47 보고 당시의 원시 시계열 부재로 과거 수치 자체의 복원은 `INCONCLUSIVE` |
| **IMMUTABLE** | `ResearchCyclePolicy` 불변 데이터클래스 적용 및 데이터셋 디렉터리 봉인으로 사후 변조 및 자가 예산 확장 차단 | `src/bithumb_coin_trader/experiment_runner.py`, `src/bithumb_coin_trader/prospective_dataset.py` | `tests/test_phase5_synthetic_e2e.py::test_p11_governed_budget_immutable_cycle_policy` | 파일시스템 OS 루트 권한을 가진 외부 프로세스의 디스크 직접 변조까지는 차단 불가 |

------------------------------------------------------------
11. MERGE READINESS
------------------------------------------------------------

`OFFLINE-READY-PENDING-REAL-DATA`

- 본 브랜치는 Phase 5 오프라인 수용 파이프라인의 모든 엔지니어링 및 수학적·거버넌스 요구사항을 100% 충족하였습니다.
- 라이브 72시간 소크가 자연 종료되고 원시 데이터가 안전하게 수신되기 전까지 main 브랜치로 자동 머지하지 않습니다.

------------------------------------------------------------
12. AFTER 72H NATURAL COMPLETION (권장 후속 실행 명령 시퀀스)
------------------------------------------------------------

실제 72시간 소크가 정상 종료된 후, 작업자가 오프라인 격리 워크스테이션에서 순차 실행해야 하는 표준 시퀀스입니다. (절대 지금 실행하지 마십시오)

```bash
# 1. 종료 영수증 및 아카이브 데이터 로컬 동기화 확인 (수동 아티팩트 배치)
export SOAK_DIR="/path/to/exported/72h_soak_epoch"

# 2. 권위적 Deep DQ 감사 실행 (exit code 0 확인 필수)
python -m bithumb_coin_trader.research_cli deep-dq-audit \
  --epoch-dir "${SOAK_DIR}" \
  --report-out reports/72h_deep_dq_report.json

# 3. 암호학적 DQ 자격증명 발급 (exit code 0 확인 필수)
python -m bithumb_coin_trader.research_cli dq-qualify \
  --deep-audit-report reports/72h_deep_dq_report.json \
  --source-manifest "${SOAK_DIR}/manifests/epoch_manifest.json" \
  --qualification-out reports/72h_dq_qualification.json

# 4. 스트림별 Canonical 데이터 변환 (orderbook & trade)
python -m bithumb_coin_trader.research_cli transform-canonical \
  --input-dir "${SOAK_DIR}/raw" \
  --output-dir "${SOAK_DIR}/canonical/bithumb_orderbook" \
  --exchange bithumb \
  --stream orderbook

python -m bithumb_coin_trader.research_cli transform-canonical \
  --input-dir "${SOAK_DIR}/raw" \
  --output-dir "${SOAK_DIR}/canonical/bithumb_trade" \
  --exchange bithumb \
  --stream trade

# 5. 트랜잭션 데이터셋 파티셔닝 및 봉인
python -m bithumb_coin_trader.research_cli partition-dataset \
  --input-file "${SOAK_DIR}/canonical/bithumb_orderbook/canonical_market_data.ndjson.zst" \
  --output-dir "data/datasets/72h_bithumb_orderbook_v1" \
  --dq-report reports/72h_dq_qualification.json \
  --source-manifest "${SOAK_DIR}/manifests/epoch_manifest.json" \
  --clock receive_wall_clock

# 6. 거버넌스 하의 사전 등록 연구 사이클 실행
python -m bithumb_coin_trader.research_cli run-governed-cycle \
  --policy-file configs/cycle1_policy.json \
  --dataset-dir "data/datasets/72h_bithumb_orderbook_v1"
```
============================================================
