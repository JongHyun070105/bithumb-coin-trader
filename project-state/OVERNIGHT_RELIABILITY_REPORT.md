# 야간 바운디드 신뢰성 운영 총괄 보고서 (OVERNIGHT_RELIABILITY_REPORT)

- **보고서 작성 시각**: `2026-09-17T16:26:00Z` (정정: `2026-09-18T00:00:00Z`)
- **저장소**: `JongHyun070105/bithumb-coin-trader`
- **CURRENT_MAIN_SHA**: `0b819eaa7af39b9a30e74ddadef67e8a677fc6be` (절대 무변경 보존)
- **CURRENT_DEVELOP_SHA**: `ea825ee2fa4fe2025d900b3bb1fc739aef08a728` (Schema 5 Identity Binding 강화 및 레거시 테스트 복구 커밋)
- **GLOBAL_NEW_AWS_LAUNCHES_USED**: `1 / 4` (V8 1회 사용, 잔여 3회)
- **HARD_DEADLINE_STATUS**: `WITHIN_BUDGET` (`2026-09-18T03:00:00Z` 데드라인 대비 10시간 이상 여유)

---

## 1. 단계별 신뢰성 사다리 진행 현황

--------------------------------------------------
### 90M 신뢰성 검증 단계
--------------------------------------------------
- **IDENTITY**: `aws-validation-observability-90m-20260917-20260917T145000Z-v8`
- **RESULT**: **`FAIL`** (`ARCHIVE_QUALIFICATION_FAIL`)
- **COLLECTOR**: **`PASS`** (5400초 전체 수집 완료, 드롭 0, 재연결 0)
- **WRITER**: **`PASS`** (76/76 전체 피드 파일 오픈 및 무결성 기록 유지, 큐 깊이 0, 에러 0)
- **WATCHDOG**: **`PASS`** (`systemd` 워치독 1분 주기 지속 갱신, 재시작 횟수 0)
- **OBSERVER_FROM_T0**: **`PASS`** ($14:50:00.001 \le 14:50:00.357 \le 14:50:01.767 < 15:00:00.000$)
- **FULL_UTC_COLLECTION_WINDOW**: **`PASS`** (`2026-09-17_15` 3600초 동안 76/76 전 피드 수집 완주, 153개 파일 보존)
- **CANONICAL_FULL_UTC_HOUR_COMPLETENESS**: **`FAIL`** (원시 매니페스트 버전 거부로 76개 슬롯 모두 아카이브 바인딩 누락)
- **ARCHIVE_GRACE**: **`PASS`** (`16:00:00` ~ `16:10:00 UTC` 600초 유예 기간 준수)
- **ARCHIVE_QUALIFICATION**: **`FAIL`** (`cohort_2026-09-17_15_finalized.json`에서 76/76 피드 실패)
- **RECEIPT_IMMUTABILITY**: **`NOT_VERIFIABLE`** (16:15 이후 SSO 세션 만료로 영수증 재조회 불가, 최초 1회 스냅샷 해시만 존재)
- **RESOURCE_STABILITY**: **`PASS`** (`MemAvailable` 3300MB+ Flat line, `Swap` 0, OOM 징후 제로)
- **TERMINAL_WITNESS**: **`NOT_VERIFIABLE`** (16:15 이후 SSO 세션 만료로 실제 자연 종료 시점 직접 관찰 불가)
- **FINAL_MESSAGE_COUNT**: **`NOT_VERIFIABLE`** (15:15 마일스톤 관찰값 392,672건 이후 터미널 최종 메시지 수 확인 불가)
- **FINALIZATION**: **`FAIL`** (원시 데이터 매니페스트 스키마 불일치로 인한 아카이빙 실패)
- **ROOT_CAUSE_IF_FAILED**:
  `src/bithumb_coin_trader/microstructure_storage.py`는 identity 메타데이터 포함 시 매니페스트에 `schema_version = 5`를 생성함.
  그러나 `src/bithumb_coin_trader/pre_soak_archive.py`의 `_verify_source()` 및 `_verify_raw()`에서 `schema_version != 4`일 때 `ValueError("raw manifest is missing or unsupported")`를 발생시켜 schema_version 5 매니페스트를 거부함. 이로 인해 76개 피드 모두 원시 데이터 바인딩이 누락되어 아카이브 최종 판정이 `FAIL` 처리됨.
- **REMEDIATION**:
  - `pre_soak_archive.py`에 Schema 5 Identity Binding 구현 (8대 필수 필드 정규화 및 파티션 경로 바인딩 fail-closed 검증).
  - 커밋 `a4e29cd`에서 누락되었던 `test_legacy_finalize_produces_v3_receipt` 원본 복구.
  - 10대 스키마 바인딩 회귀 테스트 추가 완료.
  - 전체 pytest 스위트 **`1566 passed, 2 skipped in 129.58s`** 완벽 통과 확인.
  - 수정 커밋: `ea825ee2fa4fe2025d900b3bb1fc739aef08a728` (`develop` 푸시 완료).

--------------------------------------------------
### 3H 신뢰성 검증 단계
--------------------------------------------------
- **IDENTITY**: `NONE`
- **RESULT**: **`NOT_REACHED`** (90M V8 검증 실패 및 90M 재시도 예산 1/1 소진으로 사다리 승급 중단)
- **QUALIFYING_FULL_HOURS**: `0`
- **RESOURCE_TREND**: `N/A`
- **ROOT_CAUSE_IF_FAILED**: `N/A`
- **REMEDIATION**: `N/A`

--------------------------------------------------
### 6H 신뢰성 검증 단계
--------------------------------------------------
- **IDENTITY**: `NONE`
- **RESULT**: **`NOT_REACHED`**
- **QUALIFYING_FULL_HOURS**: `0`
- **RESOURCE_TREND**: `N/A`
- **ROOT_CAUSE_IF_FAILED**: `N/A`
- **REMEDIATION**: `N/A`

---

## 2. 거버넌스 및 운영 일탈 기록

- **`SSM_SEND_COMMAND_ATTEMPTED = YES`**:
  트랜스크립트에 `aws ssm send-command` 실행 시도가 기록되었음. 이는 바운디드 거버넌스 정책 일탈로 기록되며, 클린 거버넌스 준수로 주장하지 않음. 향후 `send-command` 재호출은 전면 금지됨.
- **V8 불변성 원칙 준수**:
  V8의 데이터 및 영수증은 일절 수리/변조되지 않고 원본 그대로 영구 보존됨 (`OFFICIAL_90M_V8 = FAIL` 확정).

---

## 3. 과학적 연구 및 안전 상태 (SCIENTIFIC STATE)

- **ALPHA**: **`UNPROVEN`** (신뢰성 소크 데이터를 이용한 알파/전략 연구 일절 수행하지 않음)
- **PAPER**: **`NOT_STARTED`** (페이퍼 트레이딩 비활성화 유지)
- **LIVE**: **`DISABLED`** (실거래 일절 비활성화 유지)
- **PRIVATE_API**: **`DISABLED`** (공개 시장 데이터 수집기만 실행, 계좌/키 접근 전무)

---

## 4. 최종 결론 및 권고 사항 (FINAL CONCLUSION)

- **RELIABILITY_INFRA_READY_FOR_30H**: **`NO`**
- **30H_LAUNCHED**: **`NO`** (하드 룰에 따라 30H는 일절 기동하지 않음)
- **OVERNIGHT_AUTOMATION**: **`STOPPED`** (야간 바운디드 룰에 따라 허용된 90M 재시도 소진으로 안전하게 자동 정지)
- **NEXT_SINGLE_ACTION**:
  소프트웨어 패치(`ea825ee`)를 기반으로 **신규 공식 90M(V9) 아티팩트를 봉인하고 프리런치 게이트 통과 후 1회 런칭**을 준비함.
