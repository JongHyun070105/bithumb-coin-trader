# Bithumb Coin Trader — 통합 프로젝트 상태 및 포스트 소크 인계 명세
(Project Status & Post-Soak Handoff Specification)

**기준 시각 (Current Time):** 2026-09-06T23:10:00+09:00 (UTC 14:10:00)  
**적용 브랜치 (Authoritative Branch):** `main`  
**현재 HEAD 커밋:** `b73f0208b92caa9af63240d96317689468b8d785`  

---

## 1. 프로젝트 공식 상태 (Official Project Status)

```
===============================================================================
OFFICIAL PROJECT STATUS MATRIX
===============================================================================

OFFLINE TOOLING:
MERGED TO MAIN

OFFLINE SYNTHETIC VERIFICATION:
PASS (Bounded memory behavior observed over tested synthetic scale range)

72H LIVE SOAK:
RUNNING / FINAL RESULT PENDING

REAL 72H FINAL AUDIT:
NOT RUN

ACTUAL START EVIDENCE:
REQUIRED / NOT YET INGESTED (FAIL-CLOSED)

REAL DATA DQ:
NOT RUN

MICROSTRUCTURE ALPHA:
UNPROVEN

V4 / V6 BASELINE:
FROZEN RESEARCH BASELINE — ALPHA UNPROVEN

V8 / V8.1:
REJECTED

PAPER TRADING:
NOT STARTED

LIVE TRADING:
DISABLED

PRIVATE API:
DISABLED
===============================================================================
```

> [!IMPORTANT]
> 오프라인 도구의 main 브랜치 통합은 **실제 72H 데이터의 품질 통과**나 **알파 검증**을 의미하지 않습니다.  
> 실제 무인 수집의 완료 시점까지 모든 실시간 시스템 및 AWS 환경은 완전히 동결·격리되어야 합니다.

---

## 2. 72H 포스트 소크 인계 체크리스트 (Post-Soak Handoff Checklist)

72시간 무인 수집(259,200초)이 자연 종료된 후 순차적으로 수행해야 하는 14단계 표준 작업 절차입니다.  
**수집 종료 이전에는 어떠한 단계도 조기 실행하지 않습니다.**

1. **자연 완료 검증 (Prove natural 259,200-second completion)**:
   - 인스턴스/서비스의 72시간 지속 가동 및 정상 종료 타임스탬프 대조.
2. **권위적 실제 시작 시각 증거 확보 (Obtain authoritative actual-start evidence)**:
   - 런칭 시각이 아닌 실제 수집기 프로세스 기동 증거 아티팩트(`actual_start_evidence.json`) 확보.
3. **권위적 실제 종료/완료 증거 확보 (Obtain authoritative actual-end / completion evidence)**:
   - 정상 수집 완료 영수증 및 프로세스 종료 로그 증거 확보.
4. **불변 에포크 증거 보존/내보내기 (Preserve / export immutable epoch evidence)**:
   - 원시 파티션 파일, WAL, 영수증 파일 원본을 읽기 전용으로 안전하게 아카이브 보존.
5. **런타임 씰 + 런칭 출처 검증 (Verify runtime seal + launch provenance)**:
   - `runtime.json` 및 `launch-provenance.json`의 커밋, 핑거프린트, 런 ID 암호학적 해시 대조.
6. **공식 에포크 계약서 합성 (Compose official epoch contract)**:
   - `python3 scripts/compose_epoch_contract.py` 실행 (실제 시작 시각 증거 바인딩).
7. **봉인된 에포크 루트 매니페스트 빌드 (Build sealed epoch root)**:
   - `python3 scripts/build_epoch_manifest.py --contract ... --strict` 실행 (전체 원시 SHA 재계산).
8. **심층 데이터 품질 감사 수행 (Run deep DQ audit)**:
   - `python3 scripts/audit_72h_soak.py --mode official` 실행 (76개 피드 전수 스트리밍 검증).
9. **DQ 적격성 판정 아티팩트 생성 (Generate DQ qualification)**:
   - `python3 -m bithumb_coin_trader.research_cli dq-qualify` 실행 (`DQ_PASS`, `degraded_count == 0` 필수).
10. **루트 등록 원시 데이터 캐노니컬 변환 (Canonicalize root-listed data)**:
    - `python3 -m bithumb_coin_trader.research_cli transform-canonical` 실행 (미등록 원시 주입 엄격 거부).
11. **캐노니컬 루트 매니페스트 빌드 (Build canonical root)**:
    - 캐노니컬 파티션 해시 서명 및 삼자 일관성(`canonical == DQ == actual_epoch`) 확립.
12. **연구용 데이터셋 분할 생성 (Create research dataset)**:
    - `python3 -m bithumb_coin_trader.research_cli partition-dataset` 실행 (출처 메타데이터 10종 봉인).
13. **홀드아웃 데이터셋 암호학적 봉인 유지 (Keep holdout sealed)**:
    - 연구 탐색(Discovery/Validation) 중 홀드아웃 데이터 절대 비열람·비접촉.
14. **사전 등록된 가설 연구 착수 (Only then begin preregistered research)**:
    - `docs/MICROSTRUCTURE_RESEARCH_PREREGISTRATION_V1.md`에 명시된 규칙에 따라 통계 검정 시작.

---

## 3. 실제 시작 시각 증거 아티팩트 명세 (Actual Start Evidence Specification)

현재 공식 계약서 합성(`compose_epoch_contract.py`)은 실제 시작 시각 증거가 주어지지 않을 경우 추측을 방지하기 위해 **Fail-Closed (`ACTUAL_START_EVIDENCE_MISSING`, exit 2)** 상태로 안전하게 차단되어 있습니다.  
포스트 소크 시점에 수집되어 제공되어야 하는 아티팩트의 스키마 명세는 다음과 같습니다:

- **예상 파일명**: `actual_start_evidence.json`
- **필수 스키마 필드**:
  ```json
  {
    "schema_version": 1,
    "collector_epoch": "epoch-aws-72h-soak-20260905",
    "collector_run_id": "run-aws-72h-soak-20260905-8017b83e",
    "actual_start_time_utc": "YYYY-MM-DDTHH:MM:SS.ffffffZ",
    "start_evidence_type": "SYSTEMD_SERVICE_START | PROCESS_EXEC_START | FIRST_RAW_RECORD",
    "source": "journalctl_systemd_start | raw_envelope_record_zero",
    "runtime_commit": "e9e4be4db086706e57ba51c14a2432a106526fc8",
    "runtime_fingerprint": "...",
    "captured_at_utc": "YYYY-MM-DDTHH:MM:SSZ",
    "evidence_sha256": "..."
  }
  ```

> [!CAUTION]
> - `actual_start_time_utc`를 사전에 임의 추측하거나, `launch-provenance.json`의 `created_at_utc`를 대용하여 입력해서는 안 됩니다.
> - 실제 인스턴스의 런타임 증거(저널 로그, 첫 레코드의 수신 단조 시각 및 로컬 타임스탬프)로부터 정밀 추출된 값이어야 합니다.

---

## 4. 라이브 트레이딩 및 모델 격리 상태 (Trading & Model Isolation)

- **알파 상태**: 마이크로스트럭처 피처의 통계적 알파는 **미검증(UNPROVEN)** 상태입니다.
- **모의 투자 (Paper Trading)**: 실행 엔진과 주문 파이프라인의 오프라인 테스트는 통과하였으나, 실시간 피드 기반 페이퍼 트레이딩은 시작되지 않았습니다(`NOT STARTED`).
- **실주문 (Live Trading)**: `order_transport.py` 내 `BithumbLiveOrderTransport`는 안전 가드에 의해 엄격히 봉인되어 있으며, 주문 실행 경로는 전면 차단(`DISABLED`) 상태입니다.
- **프라이빗 API 키**: 어떠한 실거래 키도 저장되어 있지 않으며(`DISABLED`), 보안 감사 기준을 완벽 준수합니다.
