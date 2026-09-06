# Phase 2 Claim Reconciliation — Forensic Audit (Phase 2.5)

**Auditor**: Phase 2.5 Forensic Hardening Sprint  
**Date**: 2026-09-05  
**Base commit**: `ff1b971bd00a0b0c61845fa7a4d615c505e52881`  
**Forensic branch**: `codex/72h-offline-phase2-forensic-20260905`

---

## 범례

| VERDICT | 의미 |
|---------|------|
| ✅ CONFIRMED | 코드가 주장을 실질적으로 지지함 |
| ⚠️ OVERCLAIM | 주장이 구현보다 넓거나 강함 |
| ❌ FALSE | 주장이 구현과 직접 모순됨 |
| 🔧 FIXED | Phase 2.5에서 수정됨 |

---

## 1. experiment_runner.py

### 1.1 "Append-only atomic trial reservation"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: `reserve_trial()`이 `self._reserved_trials.add()`에만 추가하고 파일에 기록하지 않음. 프로세스 크래시 시 예약 소멸.
- **FIX**: `ReservationRecord`를 `.reservations.json`에 원자적으로 기록. 재시작 시 복원.
- **LIMITATION**: 파일 수준 잠금(flock) 미구현 — 진정한 다중 프로세스 원자성은 미보장.

### 1.2 "Cryptographic hash-chain ledger (tamper-evident SHA-256 links)"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ✅ CONFIRMED (tamper-evident) / ⚠️ OVERCLAIM (immutable)
- **EVIDENCE**: SHA-256 체인 구현 확인. 단, 파일 자체 삭제는 감지 불가.
- **LIMITATION**: 로컬 가변 파일의 SHA-256 체인은 tamper-evident이지 immutable이 아님. 불변성은 외부 write-once 스토리지 필요.

### 1.3 "Family budget enforcement (hard stop at N <= 9 trials)"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: `count_family_trials()`가 `_entries` (COMPLETED만) 카운트. RESERVED/FAILED 상태의 시도 미포함.
- **FIX**: `count_family_trials()`가 `_reservations` 딕셔너리 전체 카운트. 모든 상태 포함.

### 1.4 "Mandatory preregistration gating: No experiment runs without a valid manifest"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ⚠️ OVERCLAIM → 🔧 FIXED
- **EVIDENCE**: `record_trial()`이 `reserve_trial()` 선행 없이 호출 가능했음. manifest만 있으면 gating 우회 가능.
- **FIX**: `record_trial()`이 `_reservations`에 trial_id 존재 여부 확인. 없으면 `ReservationRequiredError`.

### 1.5 "Dataset role gating preventing lookahead/holdout leakage"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ⚠️ OVERCLAIM → 🔧 FIXED
- **EVIDENCE**: `is_final_verification=True`로 HOLDOUT 접근 우회 가능. 누구든 True를 전달하면 됨.
- **FIX**: `ResearchCycleState` 상태 머신 도입. HOLDOUT 접근은 HOLDOUT_AUTHORIZED 상태에서만 허용.

---

## 2. risk_engine.py

### 2.1 "Comprehensive pre-flight checks: max notional, gross exposure, spread filter, expected slippage"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ❌ FALSE (slippage) → 🔧 FIXED
- **EVIDENCE**: `max_slippage_bps = 30.0` 이 `RiskEngineConfig`에 정의되어 있으나 `evaluate_preflight()`에서 단 한 번도 읽히지 않음.
- **FIX**: `_estimate_slippage_bps()` 메서드 추가. `evaluate_preflight()`에서 실제로 체크.
- **LIMITATION**: 슬리피지 추정은 best bid/ask 기반 단순 모델. Walk-the-book 시뮬레이션이 아님.

### 2.2 "Ternary risk verdicts: ALLOW, REJECT, HALT"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ✅ CONFIRMED
- **EVIDENCE**: 코드에서 세 가지 verdict 모두 올바르게 반환됨.

### 2.3 "Fail-closed default: any missing, NaN, Inf, or invalid input results in REJECT or HALT"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ⚠️ OVERCLAIM (부분 구현) → 🔧 FIXED
- **EVIDENCE**: NaN/Inf 체크는 있었으나, side 유효성, 0/음수 notional, 0/음수 equity 체크 없음.
- **FIX**: 엄격한 side 유효성 ("BUY"/"SELL" 외 → HALT), notional ≤ 0 → HALT, equity ≤ 0 → HALT 추가.

### 2.4 "Immutable audit trail generation for every evaluation"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ❌ FALSE (immutable)
- **EVIDENCE**: `audit_log: list[RiskAuditRecord] = []` — 메모리 내 가변 리스트. 재시작 시 소실. 수정 가능.
- **STATUS**: 문서에 LIMITATION으로 명시됨. 진정한 불변성은 외부 영구 저장소 필요.

### 2.5 SELL 노출도 계산 오류
- **CLAIM SOURCE**: 코드 behavior (implicit)
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: `future_exposure = (position + notional) / equity` — side 무관 동일. SELL은 exposure 감소해야 함.
- **FIX**: SELL은 `max(0, position - notional) / equity` 사용.

---

## 3. prospective_dataset.py

### 3.1 "Builds leakage-free research datasets"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ⚠️ OVERCLAIM
- **EVIDENCE**: 시계열 레이어에서 TRAIN/VAL/HOLDOUT 분리는 올바름. 그러나 feature-level leakage (파티션 경계에 걸친 feature 계산)는 방지하지 않음.
- **STATUS**: LIMITATION으로 명시됨. "시계열 순서 기준 leakage-free" 로 scope 제한.

### 3.2 "Cryptographic SHA-256 checksums and immutable dataset manifests"
- **CLAIM SOURCE**: 모듈 docstring
- **VERDICT**: ⚠️ OVERCLAIM (immutable)
- **EVIDENCE**: SHA-256 체크섬 구현 확인. 그러나 manifest.json은 로컬 파일 — 가변.
- **STATUS**: LIMITATION으로 명시됨.

### 3.3 비정렬 입력 silent sort
- **CLAIM SOURCE**: `partition_records_temporally()` 동작
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: `sorted_recs = sorted(records, ...)` — 시계열 역전 숨김. "leakage-free" 주장과 모순.
- **FIX**: 비정렬 입력 → `ValueError` raise. 정렬은 호출자 책임.

---

## 4. synthetic_market.py

### 4.1 "price moves after lag_steps"
- **CLAIM SOURCE**: `SignalMarketGenerator` docstring
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: 같은 loop iteration에서 imbalance 생성 + price 적용. lag_steps 파라미터도 없었음.
- **FIX**: `lag_steps` 파라미터 추가. deque 버퍼로 실제 lag 구현. lag_steps=0은 contemporaneous 명시.

---

## 5. research_cli.py / 런북 문서

### 5.1 POST_72H_OFFLINE_IMPORT_RUNBOOK.md CLI 명령어
- **CLAIM SOURCE**: 런북 문서
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: `audit-quality`, `transform-canonical`, `partition-dataset` 미존재.
- **FIX**: 세 명령어 모두 `research_cli.py`에 추가됨.

### 5.2 허구 아카이브 파일명
- **CLAIM SOURCE**: 런북 Step 1
- **VERDICT**: ❌ FALSE → 🔧 FIXED
- **EVIDENCE**: `archive_hour_001.tar.zst`~`archive_hour_072.tar.zst` 는 실제 파일명이 아님.
- **FIX**: `<EXPORTED_EPOCH_ROOT>/archive_hour_NNN.tar.zst` 플레이스홀더로 교체.

---

## 6. exchange_semantics 문서

### 6.1 "Binance → Bithumb: 중앙값 약 120ms"
- **CLAIM SOURCE**: `cross_exchange_microstructure_comparison.md`
- **VERDICT**: ❌ FALSE → 🔧 FIXED (레이블 추가)
- **EVIDENCE**: 이 프로젝트에서 실측한 값 없음. 업계 통념 기반 추정치.
- **FIX**: `[ASSUMPTION: NOT_MEASURED_BY_THIS_PROJECT]` 레이블 및 경고 박스 추가.

---

## 7. 남은 미해결 한계 (Post-Fix)

다음 한계는 이번 Phase 2.5에서 수정되지 않았으며, 향후 과제로 남아 있습니다:

1. **파일 수준 잠금 없음**: `_save_reservations()`, `_save_ledger()` 는 atomic rename으로 손상은 방지하나, 다중 프로세스 동시 쓰기 시 race condition 가능.
2. **ResearchCycleState 비영속적**: `advance_research_state()` 상태가 메모리에만 존재. 프로세스 재시작 시 PREREGISTERED로 초기화됨.
3. **슬리피지 추정 단순화**: `_estimate_slippage_bps()`는 best bid/ask 기반. Walk-the-book 없음.
4. **`audit_log` 비영속**: `RiskEngine.audit_log`는 메모리 리스트. 재시작 시 소실.
5. **`dataset_id` wall-clock 의존**: `build_and_export_dataset()` manifest의 `created_at_utc`가 wall-clock → 재현 불가 dataset_id.
6. **`read_bytes()` 대용량 파일 비적합**: `build_and_export_dataset()`에서 파일 전체를 메모리에 로드하여 SHA-256 계산. 대용량 파일에는 streaming hash 필요.
7. **`transform-canonical` 스텁**: `research_cli transform-canonical`은 실제 변환 미구현 (STUB_NOT_IMPLEMENTED).
8. **socket monkeypatch 불완전**: `conftest.py` socket 차단이 `connect_ex`, subprocess curl, async transport 등 미차단.
9. **소켓 차단 우회 가능**: `os.system`, `subprocess` 등 경로로 네트워크 접근 가능.
10. **722 테스트 통과 ≠ 정확성**: 소프트웨어가 올바름을 증명하지 않음. 구현이 계약 범위 밖에서 올바른지 알 수 없음.

---

## 요약

| 카테고리 | 발견된 버그 | Phase 2.5에서 수정 | 남은 한계 |
|---------|-----------|---------------------|----------|
| experiment_runner | 5 | 5 | 2 (파일 잠금, 상태 영속) |
| risk_engine | 3 | 3 | 2 (슬리피지 단순화, audit_log 비영속) |
| prospective_dataset | 3 | 3 | 2 (dataset_id, read_bytes) |
| synthetic_market | 1 | 1 | 0 |
| research_cli | 1 | 1 | 1 (transform stub) |
| 문서 | 3 | 3 | 0 |
| **합계** | **16** | **16** | **7+** |

> **722 테스트가 통과했다는 사실은 Phase 2 코드가 올바름을 증명하지 않는다.**
> 테스트는 명시적으로 검증된 계약 범위 내에서만 동작을 확인한다.
