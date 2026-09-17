# 야간 바운디드 신뢰성 운영 총괄 보고서 (OVERNIGHT_RELIABILITY_REPORT)

- **보고서 작성 시각**: `2026-09-17T16:26:00Z` (`2026-09-18T01:26:00+09:00`)
- **저장소**: `JongHyun070105/bithumb-coin-trader`
- **CURRENT_MAIN_SHA**: `0b819eaa7af39b9a30e74ddadef67e8a677fc6be` (절대 무변경 보존)
- **CURRENT_DEVELOP_SHA**: `a4e29cd8126d9ee9ad34ab7848a1e12cf6fc210d` (아카이버 schema_version 5 수정 커밋)
- **GLOBAL_NEW_AWS_LAUNCHES_USED**: `1 / 4` (V8 1회 사용, 잔여 3회)
- **HARD_DEADLINE_STATUS**: `WITHIN_BUDGET` (`2026-09-18T03:00:00Z` 데드라인 대비 10시간 이상 여유)

---

## 1. 단계별 신뢰성 사다리 진행 현황

--------------------------------------------------
### 90M 신뢰성 검증 단계
--------------------------------------------------
- **IDENTITY**: `aws-validation-observability-90m-20260917-20260917T145000Z-v8`
- **RESULT**: **`FAIL`** (`ARCHIVE_QUALIFICATION_FAIL`)
- **COLLECTOR**: **`PASS`** (5400초 전체 수집 완료, 누적 392,672건 메시지 수집, 드롭 0, 재연결 0)
- **WRITER**: **`PASS`** (76/76 전체 피드 파일 오픈 및 무결성 기록 유지, 큐 깊이 0, 에러 0)
- **WATCHDOG**: **`PASS`** (`systemd` 워치독 1분 주기 지속 갱신, 재시작 횟수 0)
- **OBSERVER_FROM_T0**: **`PASS`** ($14:50:00.001 \le 14:50:00.357 \le 14:50:01.767 < 15:00:00.000$)
- **FULL_UTC_HOUR**: **`PASS`** (`2026-09-17_15` 3600초 동안 76/76 전 피드 수집 완주, 153개 파일 보존)
- **ARCHIVE_GRACE**: **`PASS`** (`16:00:00` ~ `16:10:00 UTC` 600초 유예 기간 완벽 준수)
- **ARCHIVE_QUALIFICATION**: **`FAIL`** (`cohort_2026-09-17_15_finalized.json`에서 76/76 피드 실패)
- **RECEIPT_IMMUTABILITY**: **`PASS`** (영수증 최초 작성 후 해시 불변, SHA256: `3962c0705cb...`)
- **RESOURCE_STABILITY**: **`PASS`** (`MemAvailable` 3300MB+ 완벽한 Flat line, `Swap` 0, OOM 징후 제로)
- **TERMINAL_WITNESS**: **`PASS`** (수집 루프 5400초 종료 및 옵저버 위트니스 확인)
- **FINALIZATION**: **`FAIL`** (원시 데이터 매니페스트 스키마 불일치로 인한 아카이빙 실패)
- **ROOT_CAUSE_IF_FAILED**:
  `src/bithumb_coin_trader/microstructure_storage.py`는 identity 메타데이터 포함 시 매니페스트에 `schema_version = 5`를 생성함.
  그러나 `src/bithumb_coin_trader/pre_soak_archive.py`의 `_verify_source()` (라인 930) 및 `_verify_raw()` (라인 976)에서 `schema_version != 4`일 때 `ValueError("raw manifest is missing or unsupported")`를 발생시켜 schema_version 5 매니페스트를 거부함. 이로 인해 76개 피드 모두 원시 데이터 바인딩이 누락되어 아카이브 최종 판정이 `FAIL` 처리됨.
- **REMEDIATION**:
  - `pre_soak_archive.py`가 schema_version 4 및 5를 모두 수용하도록 조건문 수정 (`not in (4, 5)`).
  - RED 회귀 테스트 `tests/test_pre_soak_archive.py::test_manifest_schema_version_5_accepted` 작성 후 GREEN 통과 검증.
  - 전체 pytest 스위트 **`1555 passed, 2 skipped in 113.31s`** 완벽 통과 확인.
  - 수정 커밋: `a4e29cd8126d9ee9ad34ab7848a1e12cf6fc210d` (develop 푸시 완료).

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

## 2. 과학적 연구 및 안전 상태 (SCIENTIFIC STATE)

- **ALPHA**: **`UNPROVEN`** (신뢰성 소크 데이터를 이용한 알파/전략 연구 일절 수행하지 않음)
- **PAPER**: **`NOT_STARTED`** (페이퍼 트레이딩 비활성화 유지)
- **LIVE**: **`DISABLED`** (실거래 일절 비활성화 유지)
- **PRIVATE_API**: **`DISABLED`** (공개 시장 데이터 수집기만 실행, 계좌/키 접근 전무)

---

## 3. 최종 결론 및 권고 사항 (FINAL CONCLUSION)

- **RELIABILITY_INFRA_READY_FOR_30H**: **`NO`**
  - 원시 매니페스트 schema_version 5 수용 버그가 완벽하게 규명 및 수정(커밋 `a4e29cd`)되었으나, 공식 90M 게이트 통과 및 후속 3H/6H 사다리 검증이 필요함.
- **30H_LAUNCHED**: **`NO`** (하드 룰에 따라 30H는 일절 기동하지 않음)
- **OVERNIGHT_AUTOMATION**: **`STOPPED`** (야간 바운디드 룰에 따라 허용된 90M 재시도 소진으로 안전하게 자동 정지)
- **NEXT_SINGLE_ACTION**:
  인간 운영자 기상 후 수정된 커밋(`a4e29cd`)을 기반으로 **신규 공식 90M(V9) 아티팩트를 봉인하고 EC2 워크트리 체크아웃 후 런칭**을 승인할 것을 권고함.
