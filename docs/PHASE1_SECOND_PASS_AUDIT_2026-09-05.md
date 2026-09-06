# Phase 1 Second-Pass Code Review & Audit (2026-09-05)

## 1. 개요 및 목적

본 문서는 Phase 1(`codex/72h-offline-research-hardening-20260905`, commit `ba89d60e0cbf83a77f1bd493eff1906a4d6574b1`)에서 추가된 핵심 연구 및 검증 아티팩트에 대해 수행된 **2차 비판적 코드 리뷰(Second-Pass Audit)** 결과입니다.

643개의 테스트가 모두 통과(PASS)했다는 사실만으로 구현의 무결성이 입증되지 않습니다. 특히 테스트가 구현 자체를 참조하여 검증하는 **자체 일관성(Self-Consistency) 테스트**와 외부 또는 독립 수학적 정의에 기반한 **진정한 오라클(True Oracle) 테스트**를 엄격히 분리하여 보고합니다.

---

## 2. 평가 기준 및 테스트 분류 체계

- **TRUE ORACLE TEST**: 프로덕션 코드와 독립적으로 작성된 해석적 해(Analytic Solution) 또는 단순 독립 참조 엔진과의 교차 검증.
- **INDEPENDENT INTERNAL IMPLEMENTATION**: 프로덕션 모듈 외부에서 독립적인 로직으로 구현된 검증 도구.
- **SELF-CONSISTENCY TEST**: 프로덕션 코드가 산출한 출력을 동일한 가정이나 동일한 헬퍼를 통해 재확인하는 테스트 (구현 버그가 테스트에도 전파될 수 있음).
- **PROPERTY TEST**: 단조성, 불변량(Invariant), 보존 법칙 등 시스템이 만족해야 하는 일반적 속성 검증.
- **DOCUMENTATION CLAIM ONLY**: 코드로 강제되지 않고 문서상으로만 선언된 규약.

---

## 3. 핵심 아티팩트별 상세 감사 결과

### 1) `tests/test_backtester_oracle.py`
- **CLAIM**: 백테스터의 15대 회계 원칙(Family A ~ O) 및 인과성, 단일 계좌 공유 현금 체계가 완벽히 검증됨.
- **EVIDENCE**: 573줄에 달하는 15개 패밀리 테스트 스위트 구비.
- **TEST TYPE**:
  - Family A ~ N: **PROPERTY TEST** (무위험 수익 부재, 현금 보존, 수수료 차감, 단조성).
  - Family O: **TRUE ORACLE TEST** (`ReferenceAccountingOracle`이라는 독립된 미니 회계 엔진과 프로덕션 `Backtester`의 최종 자산 및 거래 내역 교차 일치 검증).
- **LIMITATION**:
  - 일별/시간별 캔들 레벨(OHLCV) 백테스터에 국한되며, 오더북 틱 레벨(L2 Depth Walk, 부분 체결, 레이턴시) 체결 시뮬레이션과는 격리되어 있음.
  - 슬리피지가 고정 bps로만 반영되며, 오더북 유동성 고갈에 따른 비선형 충격(Impact)을 모델링하지 못함.
- **STATUS**: **VALIDATED FOR BAR-LEVEL ACCOUNTING (LIMITED TO CANDLE DOMAIN)**

---

### 2) `scripts/audit_dsr_sensitivity.py` & `scripts/reproduce_v6_statistics.py`
- **CLAIM**: Strategy V6의 다중 검정 통계(DSR, WRC, PBO)가 77회 원장과 완전 일치하게 재현됨.
- **EVIDENCE**: `reproduce_v6_statistics.py` 실행 시 DSR = 0.9999 ~ 1.0000 출력.
- **TEST TYPE**: **SELF-CONSISTENCY TEST (WITH UNIT MISMATCH)**
- **LIMITATION (심각한 결함 발견)**:
  - **DSR 단위 불일치 버그 발견**:
    - 역사적 V6 감사(`scripts/audit_v6_portfolio_integrity.py`)는 일별 수익률($SR_{daily} pprox 0.0824$)과 일별 샤프 분산($\sigma_{daily} = \sigma_{ann} / \sqrt{365.25}$)을 사용하여 $T=1200$일 기준 DSR $pprox 61.47\%$를 도출함.
    - 반면 `reproduce_v6_statistics.py`의 `calculate_deflated_sharpe_analytical` 함수는 연율화 샤프($SR_{ann} pprox 1.5750, E[\\max] pprox 1.425$)에 일별 관측치 수인 $\sqrt{T_{days}} = \sqrt{1200}$을 곱함.
    - 즉, 연율화 샤프 차이($(SR_{ann} - E[\\max]) = 0.15$)에 연간 관측 기간인 $\sqrt{T_{years}} = \sqrt{1200 / 365.25} pprox 1.812$가 아닌 일별 관측수 $\sqrt{1200} pprox 34.64$를 곱하여 $Z$ 스코어가 $\sqrt{365.25} pprox 19.11$배 과대 팽창됨 ($z = 0.27 \\to 5.19$).
    - 이로 인해 실제 $61.47\%$였던 DSR 확률이 왜곡되어 $1.0000$으로 잘못 출력됨.
- **STATUS**: **FLAW IDENTIFIED — REQUIRES RECONCILIATION & FIX (P0.1)**

---

### 3) `src/bithumb_coin_trader/execution_simulator.py` & `tests/test_execution_simulator.py`
- **CLAIM**: 마이크로스트럭처 테이커 체결의 오더북 L2 Depth Walk, 레이턴시 지연, 슬리피지, 역선택이 결정론적으로 시뮬레이션됨.
- **EVIDENCE**: 443줄의 시뮬레이터 및 포괄적인 단위 테스트.
- **TEST TYPE**: **INDEPENDENT INTERNAL IMPLEMENTATION & PROPERTY TEST**
- **LIMITATION (잠재적 위험 식별)**:
  - `execute_with_latency`에서 레이턴시 만료 시점 이후의 스냅샷이 스트림 끝까지 없을 경우, **가장 마지막 오래된 스냅샷(`orderbook_stream[-1]`)으로 묵시적 체결(Silent Stale Fill)**을 진행하는 치명적 취약성 존재 (라인 436).
  - 최대 허용 오더북 경과 시간(`max_book_age_ms`)에 대한 방어 로직 부재.
  - 요청 파라미터에서 `requested_amount_krw`와 `requested_quantity_btc`가 동시에 주어지거나 둘 다 없는 경우에 대한 배타적 단일 모드 강제 미흡.
- **STATUS**: **SUBSTANTIALLY IMPLEMENTED BUT CONTAINS STALE-FILL VULNERABILITY (REQUIRES P4 HARDENING)**

---

### 4) `research/preregistration/microstructure_v1.json` & `docs/MICROSTRUCTURE_RESEARCH_PREREGISTRATION_V1.md`
- **CLAIM**: AWS 72H 데이터 수집 전 사전 등록(Preregistration)이 완료되어 데이터 스누핑이 방지됨.
- **EVIDENCE**: 3대 피처 패밀리(OFI, ATI, MPQI), 최대 시행 예산($N \\le 9$), 4단계 시간 분할(Discovery 24h, Validation 24h, Embargo 2h, Holdout 22h).
- **TEST TYPE**: **DOCUMENTATION CLAIM ONLY**
- **LIMITATION (학술적/통계적 결함 식별)**:
  - **72시간 데이터의 알파 검증 타당성 결여**: 72시간은 단 1회의 주말/주중 사이클도 포괄하지 못하며, 레짐 다양성이 전무함. 이를 24h/24h/22h로 쪼개어 홀드아웃 검증을 수행하는 것은 고주파 데이터의 높은 자기상관성(Serial Correlation)과 일중 계절성(Intraday Seasonality)으로 인해 표본 독립성을 심각하게 위배함.
  - **OFI 수식의 불완전성**: 단순 부호 지시함수($I$)만 적용하여 가격 레벨 점프(Price Level Change) 시의 대량 취소/호가 변경을 올바르게 반영하지 못함 (Cont et al., 2014 표준 미달).
- **STATUS**: **OVERLY AGGRESSIVE PROTOCOL — MUST BE PRESERVED AS V1 AND AMENDED VIA AMENDMENT 001 (P1)**

---

### 5) `scripts/audit_trial_ledger_provenance.py` & `evidence/research/trial_ledger_frozen_20260905.jsonl`
- **CLAIM**: 과거 77개 연구 시행의 원장 무결성 및 해시 고정이 완료됨.
- **EVIDENCE**: JSONL 파일 및 64자 SHA256 체크섬 고정.
- **TEST TYPE**: **PROPERTY TEST & CRYPTOGRAPHIC SEAL**
- **LIMITATION**:
  - 77개의 레코드가 과거에 수행된 "모든" 실험을 누락 없이 포함하고 있는지에 대한 완전성(Completeness)은 사후적으로 증명 불가능함 (`LEDGER_COMPLETENESS = NOT PROVEN`).
  - 시계열 수익률 벡터가 저장되지 않고 최종 스칼라 지표만 보존되어 있어, 전략 간 상관계수 행렬 및 유효 시행수($N_{eff}$)를 계산할 수 없음.
- **STATUS**: **CRYPTOGRAPHICALLY SEALED BUT HISTORICALLY INCOMPLETE**

---

### 6) `scripts/audit_72h_soak.py` & `scripts/verify_soak_reproducibility.py`
- **CLAIM**: 72H Soak 완료 후 데이터 무결성 및 재현성을 비대면으로 자동 감사 가능.
- **EVIDENCE**: 합성 디렉터리 및 아카이브 스캔 테스트 완비.
- **TEST TYPE**: **INDEPENDENT INTERNAL IMPLEMENTATION**
- **LIMITATION**:
  - 실 72H 데이터셋이 완결되기 전까지는 합성 목업(Mock) 데이터에 의존하여 동작하므로, 실환경의 미세 엣지 케이스(네트워크 단절 재접속 버스트, 타임스탬프 역전)에 대한 추가 스트레스 테스트가 필요함.
- **STATUS**: **OPERATIONAL FOR SYNTHETIC PIPELINES**

---

## 4. 종합 판정 및 P2 스프린트 조치 계획

1. **DSR 단위 불일치 해결 (P0.1, P0.2)**: `reproduce_v6_statistics.py`의 수식을 교정하고, 독립적인 `tests/reference_dsr.py` 오라클을 구축하여 연율화 샤프와 일별 샤프의 변환 수학을 명시적으로 고정.
2. **사전등록 V1 수정 및 강등 (P1)**: 72H 데이터셋을 알파 검증용이 아닌 "인프라 및 데이터 품질(DQ) 적격성 평가 데이터셋"으로 명시하는 Amendment 001 작성.
3. **체결 시뮬레이터 안전장치 강화 (P4)**: `max_book_age_ms` 검사 추가, 레이턴시 만료 후 데이터 부재 시 체결 거부(`INSUFFICIENT_FUTURE_DATA`), 단일 사이징 모드 강제.
