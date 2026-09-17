# AWS 유효성 검증 이력 보고서 (AWS Validation History)

- **최종 갱신 일시**: 2026-09-17T01:00:00Z
- **적용 환경**: AWS ap-northeast-2 (서울), EC2 t3.medium / gp3, S3 격리 저장소
- **원칙**: Fail-Closed, 객관적 증거 기반 판정 (Fact-First), 사후 재해석 금지

---

## 1. 유효성 검증 실행 요약 매트릭스

| 검증 ID / 에포크 | 계획 시간 | 수집 결과 | 감사 판정 | 연구 유효성 | 보존 태그 / 주요 원인 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **72h-offline (Historical)** | 72시간 | ~72h, 66 GB | **FAIL** | 부적격 | `archive/aws-72h-final`<br>생명주기 및 코호트 키 충돌로 연속성 훼손. |
| **aws-validation-45m-20260909** | 45분 | 45분 | **FAIL** | 부적격 | `archive/fresh45-final`<br>S3 아카이브 동시성 경쟁 상태(Race Condition) 발생. |
| **aws-validation-45m-20260911-1976f0f** | 45분 | 45분 | **PASS** | 수집 검증용 | `archive/fresh45-final`<br>아카이브 스케줄러 동시성 패치 및 자율 복구 검증 완수. |
| **aws-validation-30h-20260912-6576f63 (V2)** | 30시간 | 30시간 연속<br>(2,272 파일) | **PASS** | **유효 (DEV)** | `archive/v2-research-final`<br>30시간 연속 수집 및 DQ 통과(2,272 슬롯 일치). 미시구조 DEV 연구 데이터로 사용. |
| **aws-validation-30h-20260915-v3** | 30시간 | 0시간 | **FAIL** | 부적격 | `archive/aws-v3-failed-start`<br>인가 완료 후 인스턴스 런타임 시작 실패. |
| **aws-validation-30h-20260915-v4** | 30시간 | 1시간<br>(76개 커버리지) | **OVERALL: FAIL** | **부적격**<br>(`NOT_RESEARCH_USABLE`) | `archive/aws-v4-final`<br>자연 종료 시각 초과 후 포렌식 완료. 원시 데이터 0건. |

---

## 2. 세부 에포크별 포렌식 및 결론

### 1) V2 검증 (`aws-validation-30h-20260912-6576f63`)
- **수집 기간**: 2026-09-12 11:00 UTC ~ 2026-09-13 16:00 UTC (30시간)
- **수집 데이터**: 2,272개 원시 JSONL.ZST 파일 (561.8 MB), 8개 결측 (계약 허용 범위 내).
- **데이터 품질 (DQ)**: 타임스탬프 역전 0건, 피드 슬롯 무결성 전수 검증 통과.
- **연구 분할**: 18h DEV / 6h VAL / 6h TEST로 동결 분할. 본 프로젝트의 유일한 권위적 연구 데이터셋으로 활용됨.

### 2) V4 최종 포렌식 감사 (`aws-validation-30h-20260915-v4`)
- **실행 ID**: `aws-validation-30h-run-20260915T061253Z-v4`
- **인스턴스 ID**: `i-008bc503c1136349f`
- **시작 시각**: 2026-09-15 10:26:33 UTC
- **계획 종료 시각**: 2026-09-16 17:00:00 UTC
- **감사 수행 시각**: 2026-09-17 00:30:00 UTC (계획 시각 7.5시간 경과 후)
- **포렌식 사실 확인**:
  - EC2 인스턴스는 `running`, SSM 에이전트는 `Online` 상태 유지.
  - S3에 업로드된 객체는 10시 구간의 76개 커버리지 JSON 파일(534 KB)뿐이며, 마지막 S3 업로드 시각은 `2026-09-15T11:01:37Z`임.
  - 마켓 데이터 원시 파일(`RAW_MARKET_DATA`)은 S3에 **0건** 존재함.
- **최종 판정**:
  - `PROCESS: FAIL`, `ARCHIVE: FAIL`, `DQ: FAIL`, `EVIDENCE_CONTRACT: FAIL`, **`OVERALL: FAIL`**.
  - 연구 유효성: **`NOT_RESEARCH_USABLE`**.
- **조치**:
  - `evidence/aws-validation-30h-20260915-v4/post-run/v4-terminal-audit.json` 및 `final-audit.json` 작성 및 잠금.
  - 영구 주석 태그 `archive/aws-v4-final` 생성 및 푸시 완료.
  - 원격 브랜치 `codex/aws-30h-v4-remediation-preparation-20260915-3e7bcd9` 안전 삭제.
