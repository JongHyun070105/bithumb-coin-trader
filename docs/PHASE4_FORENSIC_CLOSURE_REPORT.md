# 72H OFFLINE PHASE 4 크로스 레이어 포렌식 종결 보고서 (CROSS-LAYER FORENSIC CLOSURE REPORT)

## 1. 개요 및 베이스라인 검증

- **대상 저장소**: `JongHyun070105/bithumb-coin-trader`
- **베이스 커밋 (Phase 3 HEAD)**: `061873431da2e3b10e00869afc3fe9e746b88c41`
- **작업 브랜치**: `codex/72h-offline-phase4-crosslayer-20260906`
- **AWS 및 라이브 파이프라인 격리**:
  - AWS CLI / STS / SSM / EC2 / S3 / CloudWatch 일체 호출 없음 (호출 횟수: 0)
  - 실행 중인 72H 라이브 소크 데이터 일체 열람/변조 없음 (호출 횟수: 0)
  - 완전 오프라인 연구 거버넌스 및 CLI 격리 환경에서 재현 및 수정 완료

---

## 2. 재현된 Phase 3 크로스 레이어 모순 (REPRODUCED PHASE 3 CONTRADICTIONS)

Phase 3에서 801개 테스트가 모두 통과했음에도 불구하고, 상위 계층(CLI/오케스트레이션)이 하위 계층의 불변식(Invariant)을 무력화하던 다음 모순들을 수정 전에 실패 테스트(`tests/test_phase4_regressions.py`)로 100% 재현 입증하였습니다.

1. **CLI OVERWRITE BYPASS (P0)**:
   - *Phase 3 상태*: `build_and_export_dataset(..., allow_overwrite=False)`로 하위 엔진은 덮어쓰기를 거부했으나, `research_cli partition-dataset`이 기본값으로 `allow_overwrite=True`를 전달하여 봉인된 데이터셋 디렉터리를 침묵 속에 덮어씀.
   - *재현 테스트*: `test_cli_partition_refuses_overwrite_when_output_dir_sealed` (초기 실패: 0 반환 및 기존 파일 파괴).
2. **DQ FORGED PASS & UNBOUND PROVENANCE (P1, P1.1, P1.2, P1.3)**:
   - *Phase 3 상태*: `--dq-report`가 주어졌을 때 누락된 출처 필드(`audit_code_commit`, `source_manifest_hash` 등)를 `"unknown"`으로 자동 채우고 자체 `report_hash` 무결성 검증을 건너뛰어 자가주장형 가짜 DQ_PASS를 허용함.
   - *재현 테스트*: `test_dq_evidence_rejects_unknown_provenance_defaults`, `test_dq_evidence_report_hash_tampering_detected`, `test_dq_source_manifest_hash_mismatch_rejected`.
3. **FILENAME DATASET ID (P2, P2.1)**:
   - *Phase 3 상태*: 하위 빌더는 `dataset_id`가 비어있을 때만 해시를 계산했으나, CLI가 `dataset_id = input_file.stem`을 강제 주입하여 파일명을 바꾸는 것만으로 데이터셋 신원이 변경되는 결함 발생.
   - *재현 테스트*: `test_dataset_id_is_content_addressed_not_filename`.
4. **MANIFEST FSYNC GAP (P4)**:
   - *Phase 3 상태*: 보고서에는 `temp -> fsync -> rename`으로 원자적 내구성을 주장했으나 실제 코드는 `write_text -> replace`만 수행하여 크래시 시 파일 유실 가능성 잔존.
   - *해결*: `write` -> `flush` -> `os.fsync(file)` -> `os.replace` -> `dir fsync` 표준 절차 구현.
5. **WAL COMPLETED-WITHOUT-LEDGER (P5, P5.1, P5.2, P5.3)**:
   - *Phase 3 상태*: `record_trial` 중 예약 완료 후 원장 기록 직전 크래시 발생 시, 재시작 복구 루틴이 `COMPLETED` 예약만 보고 인텐트를 삭제하여 미커밋 트랜잭션을 영구 은폐함. 또한 손상된 인텐트 JSON을 broad exception으로 무시함.
   - *재현 테스트*: `test_experiment_wal_crash_completed_without_ledger_detected`, `test_corrupt_intent_fails_closed`.
6. **STATUS MACHINE BYPASS (P6)**:
   - *Phase 3 상태*: 상태 전이 규칙은 `RESERVED -> RUNNING -> COMPLETED`였으나 `record_trial`이 `RESERVED` 상태에서 바로 완료를 허용함.
   - *재현 테스트*: `test_record_trial_requires_running_status`.
7. **HOLDOUT DOUBLE-CONSUME RACE (P7)**:
   - *Phase 3 상태*: 홀드아웃 소비(`access_dataset(HOLDOUT)`) 시 프로세스 간 락이 없어 멀티프로세스 동시 호출 시 복수의 연구자가 단일 홀드아웃을 동시 소비하는 경쟁 상태 노출.
   - *재현 테스트*: `test_holdout_access_multiprocess_race_exactly_one_succeeds`.
8. **RECEIVE TIMESTAMP FABRICATION (P8, P8.1, P8.2)**:
   - *Phase 3 상태*: 변환기가 `receive_timestamp_ms = exchange_timestamp_ms`로 가공된 로컬 수신 시간을 복사하여 레이턴시 안전성을 왜곡함.
   - *재현 테스트*: `test_transform_canonical_preserves_distinct_timestamps`.
9. **TRANSFORM PARTIAL SUCCESS (P9)**:
   - *Phase 3 상태*: 일부 레코드가 역직렬화에 실패해도 1건이라도 성공하면 exit 0을 반환하여 불완전 데이터셋이 통과됨.
   - *재현 테스트*: `test_transform_partial_rejected_exits_nonzero`.
10. **DSR RECONCILIATION ASSERTION (P11, P11.1, P11.2, P11.3)**:
    - *Phase 3 상태*: 문서에는 252일과 19.11배 인플레이션을 혼용하고, 수치 허용오차 검증 없이 단순 문자열 출력으로 종결 주장.
    - *해결*: 크립토 캘린더 데일리(365.25일, $\sqrt{365.25} \approx 19.1115$)로 주기성 통합, `--expected-dsr` 절대 오차 검증 및 JSON 리포트 자동 방출.

---

## 3. 세부 주장 검증 매트릭스 (CLAIM RECONCILIATION - P16)

| 항목 (CLAIM) | 실제 코드 증거 (ACTUAL CODE EVIDENCE) | 검증 테스트 (TEST) | 상태 (STATUS) | 한계 및 경계 (LIMITATION) |
| :--- | :--- | :--- | :--- | :--- |
| **OVERWRITE PROTECTED** | `research_cli.py` line 485: `allow_overwrite=False`, `prospective_dataset.py` line 331: `FileExistsError` | `test_cli_partition_refuses_overwrite_when_output_dir_sealed` | **RESOLVED** | 출력 디렉터리 내 기존 파일 존재 시 거부. 버전 분기 디렉터리 생성 권장 |
| **DQ SELF-ASSERTION IMPOSSIBLE** | `research_cli.py` line 354: `compute_canonical_report_hash`, `prospective_dataset.py` line 84: `validate()` 출처 필드 검사 | `test_dq_evidence_rejects_unknown_provenance_defaults`, `test_dq_evidence_report_hash_tampering_detected` | **RESOLVED** | DQ 리포트 내용의 1바이트 변조도 해시 불일치로 실패 차단. 위변조 방지 |
| **CONTENT ADDRESSED ID** | `prospective_dataset.py` line 371: 입력 데이터 해시, 스키마, 파티션 설정 결합 SHA-256 derivation | `test_dataset_id_is_content_addressed_not_filename` | **RESOLVED** | 파일명을 변경해도 동일 내용이면 동일 ID 도출, 1바이트 변경 시 ID 변경 |
| **SOURCE PROVENANCE** | `ProspectiveDatasetManifest`에 `source_manifest_hash`, `dq_report_hash`, `canonicalizer_commit` 필수 기록 | `test_p13_full_cross_layer_synthetic_pipeline` | **RESOLVED** | 합성 픽스처 및 실제 수집기 출력 간 출처 해시 체인 바인딩 완료 |
| **ATOMIC MANIFEST** | `prospective_dataset.py` line 400: `write -> flush -> os.fsync -> os.replace -> dir fsync` | `test_p13_full_cross_layer_synthetic_pipeline` | **RESOLVED** | CRASH-RESISTANT ATOMIC LOCAL WRITE 구현 (단, OS/파일시스템 수준 고유 장애 예외) |
| **CRASH CONSISTENCY** | `experiment_runner.py` line 344: 미커밋 예약 발견 시 `LedgerRecoveryError`, 트랜잭션 복구 | `test_experiment_wal_crash_completed_without_ledger_detected` | **RESOLVED** | 인텐트 손상 또는 원장 누락 시 Fail-Closed 원칙에 따라 즉각 크래시 복구 거부 |
| **MULTIPROCESS GOVERNANCE** | `experiment_runner.py` line 639: `_exclusive_lock(self._lock_file)` 원자적 상태 갱신 | `test_holdout_access_multiprocess_race_exactly_one_succeeds` | **RESOLVED** | 멀티프로세스 동시 접근 시 정확히 1개 프로세스만 통과, 후속 프로세스는 거부 |
| **CANONICAL TRANSFORM** | `research_cli.py` line 225: 원시 수신 타임스탬프 보존, 미존재 시 `None` 매핑 | `test_transform_canonical_preserves_distinct_timestamps` | **RESOLVED** | 거래소 시각 복사 금지. 오더북 스트림 지원 한정 (Trade/Ticker 미포함) |
| **DSR RESOLVED** | `reproduce_v6_statistics.py`: 365.25 주기성 기준 수치 허용오차 ($\Delta < 0.005$) 검증 | `test_p11_dsr_numerical_assertion_and_tolerance`, `test_p11_3_dsr_reference_vs_production_agreement` | **RESOLVED (ANALYTICAL)** | 분석적 요약값 일치 입증. 단, 원장 내 per-bar 시계열 부재로 원시 시계열은 `INCONCLUSIVE` |

---

## 4. 변이 감도 검증 결과 (P15 MUTATION SENSITIVITY PASS)

코드베이스의 9가지 핵심 불변식을 국소 변이(Mutation)시키고 회귀 테스트가 이를 즉시 감지하여 차단하는지 검증 완료하였습니다:

1. `allow_overwrite=True` 변이: `test_cli_partition_refuses_overwrite_when_output_dir_sealed`에서 **감지 성공 (FAIL 확인)**
2. `accept DQ unknown fields` 변이: `test_dq_evidence_rejects_unknown_provenance_defaults`에서 **감지 성공 (FAIL 확인)**
3. `skip report hash check` 변이: `test_dq_evidence_report_hash_tampering_detected`에서 **감지 성공 (FAIL 확인)**
4. `use filename dataset ID` 변이: `test_dataset_id_is_content_addressed_not_filename`에서 **감지 성공 (FAIL 확인)**
5. `copy exchange ts into receive ts` 변이: `test_transform_canonical_preserves_distinct_timestamps`에서 **감지 성공 (FAIL 확인)**
6. `ignore completed without ledger` 변이: `test_experiment_wal_crash_completed_without_ledger_detected`에서 **감지 성공 (FAIL 확인)**
7. `remove holdout lock` 변이: `test_holdout_access_multiprocess_race_exactly_one_succeeds`에서 **감지 성공 (FAIL 확인)**
8. `return 0 on transform rejected rows` 변이: `test_transform_partial_rejected_exits_nonzero`에서 **감지 성공 (FAIL 확인)**
9. `remove DSR numerical tolerance assertion` 변이: `test_p11_dsr_numerical_assertion_and_tolerance`에서 **감지 성공 (FAIL 확인)**

모든 변이는 테스트 실패 확인 후 완전하게 원상 복구되었습니다.

---

## 5. 최종 테스트 스위트 및 정적 분석 결과

- **전체 pytest 스위트**: 817 passed, 2 skipped, 128 subtests passed (0 failures, 100% PASS)
- **Phase 4 전용 회귀 스위트**: 16 passed (0 failures, 100% PASS)
- **git diff --check**: 클린 통과 (0 errors, trailing whitespace / EOF newline 없음)
- **비밀값 및 개인 식별 경로 스캔**: 클린 통과 (API key, 토큰, AWS 자격증명, 개인 파일 경로 0건)
- **AWS 및 라이브 파이프라인 무접근 확인**: 100% 격리 준수
