# 프로젝트 복구 및 정찰 보고서 (Project Recovery Report)

**작성 일시**: 2026-09-17 09:28:30 KST (2026-09-17T00:28:30Z)  
**현재 브랜치**: `develop`  
**현재 HEAD**: `4580ba0` (`origin/develop`, `main`, `origin/main`과 동일 SHA)

---

## 1. Git 및 브랜치 상태

- **HEAD 및 브랜치 동기화**: `develop`과 `main`은 동일한 커밋 `4580ba0`에 위치하며 원격 저장소와 완벽히 동기화되어 있음.
- **작업 트리**: `test-results/` 디렉토리는 절대 수정/삭제하지 않고 그대로 보존함. 그 외 작업 트리는 깨끗함.
- **원격 브랜치 현황 (총 4개 원격 추적 브랜치)**:
  - `origin/main` (기본 브랜치)
  - `origin/develop` (활성 개발 브랜치)
  - `origin/codex/aws-30h-v4-remediation-preparation-20260915-3e7bcd9` (V4 문서/준비)
  - `origin/gemini/dashboard-local-api-ledger-e2e-20260908` (과거 대시보드 브랜치)
- **아카이브 태그 (7개)**:
  - `archive/aws-72h-final`
  - `archive/aws-v2-failure`
  - `archive/aws-v3-failed-start`
  - `archive/aws-v3-launch-auth`
  - `archive/fresh45-final`
  - `archive/pre-v2-research-infra`
  - `archive/v2-research-final`

---

## 2. 최근 주요 변경 이력 (Commits 0.3)

1. **`8ab90f2` vs `6cb81a0` (V4 판정 정정)**:
   - 커밋 `8ab90f2`에서 V4가 아직 동작 중인 상태(계획 종료 시각 이전)에서 조기 FAIL을 선언한 중대한 절차적 오류가 발생함.
   - 커밋 `6cb81a0`에서 해당 조기 판정을 공식 `INVALID` 처리하고, 프로세스 자연 종료 전까지 최종 판정을 보류함.
2. **Maker Simulator 도입 (`52169f6`)**:
   - 수동 주문(Maker)의 체결 가능성을 모델링하는 `src/bithumb_coin_trader/maker_simulator.py` 작성됨 (현재 BUY 중심).
3. **Binance Adapter 보완 (`b011626`, `3b32383`, `d4c950e`)**:
   - Binance diff-depth에서 `exchange_ts`가 null인 현상 대응 및 bids/asks 배열 파싱 지원.
4. **대시보드 단일 진실 공급원 및 API 구축 (`12f790c`, `9b6729e`, `bcd4a62`)**:
   - 추적 아티팩트 기반 상태 해석기(`project_state.py`) 및 읽기 전용 HTTP API(`dashboard_api.py`) 도입.

---

## 3. 과학적 상태 (Scientific State)

- **ALPHA**: `UNPROVEN` (검증되지 않음)
- **PAPER TRADING**: `NOT STARTED`
- **LIVE TRADING**: `DISABLED` (Fail-Closed 엄격 적용)
- **PRIVATE API**: `DISABLED` (계좌/주문/잔고 접근 불가)

---

## 4. V2 상태 및 사실 확인 (V2 Facts)

- **데이터셋 식별자**: `aws-validation-30h-20260912-6576f63` (30시간)
- **DQ 계약 및 실측**: 총 2,280 논리 슬롯 중 2,272 DATA_PRESENT, 8 UNKNOWN_MISSING (완벽 일치)
- **시간 분할 (Chronological Split)**: 18h DEV / 6h VAL / 6h TEST
- **Taker 결과**:
  - H1(오더북 불균형), H2(체결 흐름), H3(마이크로프라이스)는 탐색적 예측력(IC 최대 0.33)을 보였으나,
  - 실체결(Taker) 비용(1.9 ~ 8.8 bps)이 예측 엣지를 초과하여 수수료 0% 프로모션 환경에서도 손실 발생.
  - **최종 결론**: `NO EXECUTABLE TAKER CANDIDATE` (실행 가능한 Taker 후보 없음, Validation 미진입).

---

## 5. V4 식별자 및 상태 (V4 Identity & State)

- **Epoch**: `aws-validation-30h-20260915-v4`
- **Run ID**: `aws-validation-30h-run-20260915T061253Z-v4`
- **실제 시작 시각**: `2026-09-15T10:26:33.652102Z`
- **계획 종료 시각**: `2026-09-16T17:00:00Z`
- **현재 시각 (2026-09-17T00:28Z)**: 계획 종료 시각으로부터 7시간 이상 경과함.
- **AWS 실측 현황 (Read-Only)**:
  - EC2 인스턴스(`i-008bc503c1136349f`): `running`
  - SSM Ping: `Online`
  - S3 아카이브: `coverage/2026-09-15_10` (단 1시간분 76개 객체, 534KB)만 존재하며, 원시 데이터(RAW) 객체는 0개임.
  - 2026-09-15 11:01 UTC 이후 어떠한 S3 쓰기나 추가 아카이브 활동도 발생하지 않음.

---

## 6. 발견된 모순 및 주요 결함 (Contradictions & Findings)

1. **V4 S3 데이터 결핍 모순**:
   - EC2 인스턴스와 SSM 에이전트는 계속 `running`/`Online` 상태이나, S3에는 시작 첫 시간(2026-09-15 10시) 이후 30시간 동안 데이터가 전혀 업로드되지 않음.
   - 벽시계 시간이 이미 계획 종료 시각(17:00 UTC)을 7시간 초과했으므로 Phase 1에서 터미널 포렌식 감사를 즉시 수행하여 확정해야 함.
2. **Binance 어댑터의 타임스탬프 혼동 (Scientific Integrity Issue)**:
   - `adapters.py`에서 Binance 오더북의 `exchange_ts`가 null일 때 `exchange_ms = local_recv_ms`로 대체하고 있음.
   - 이는 거래소 발생 시각과 로컬 수신/가용 시각을 동일시하는 과학적 오류임. `exchange_timestamp_ms`는 `None`을 허용하고 `availability_timestamp_ns`를 분리 유지해야 함.
3. **Maker Simulator 감사 필요성**:
   - 현재 `maker_simulator.py`는 BUY 방향만 존재하고 SELL 방향이 미구현 상태임.
   - 보수적(Conservative) 체결 모델에서 큐 대기 물량(`queue_ahead`) 계산식에 차원 불일치 버그가 존재함.

---

## 7. 저장소 및 테스트 기준선 (Storage & Tests)

- **로컬 저장 용량**: 74.8 GB(98.4%) 정리 완료되어 현재 저장소 전체 크기 약 890 MB 유지.
- **테스트 기준선**: 연구 인프라 및 메이커 시뮬레이터 테스트(116개) 0.22초 내 ALL PASS 확인 완료.

---

## 8. 다음 실행 단계 (Next Execution Phase)

- **Phase 1: V4 터미널 포렌식 (V4 Terminal Forensic)** 착수
  - S3 전수 재귀 인벤토리 생성
  - 인프라 및 데이터셋 연구 가용성 판정 (`NOT_RESEARCH_USABLE` 여부 확인)
  - V4 최종 불변 감사 문서 및 아카이브 태그 발행
- **Phase 2: 연구 및 엔지니어링 실행**
  - 메이커 시뮬레이터 감사 및 보수적/기준 모델 완성 (BUY/SELL 대칭, 큐 모델 정밀화)
  - Binance/타임스탬프 계약 수정 및 인과적 교차 거래소 연구
  - 대시보드/API 완합 및 최종 브랜치 정리
