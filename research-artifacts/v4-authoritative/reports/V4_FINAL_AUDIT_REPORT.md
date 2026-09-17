# AWS V4 검증 최종 터미널 포렌식 감사 보고서 (V4 Final Terminal Audit Report)

**감사 일시**: 2026-09-17 09:30:00 KST (2026-09-17T00:30:00Z)  
**상태**: 최종 불변 감사 (Final Immutable Terminal Audit)  
**종합 판정 (Overall Verdict)**: **FAIL**  
**데이터셋 연구 가용성 (Research Usability)**: **NOT_RESEARCH_USABLE**

---

## 1. 실행 식별 정보 (Run Identity)

| 항목 | 값 |
|---|---|
| **Collector Epoch** | `aws-validation-30h-20260915-v4` |
| **Collector Run ID** | `aws-validation-30h-run-20260915T061253Z-v4` |
| **Runtime Software Commit** | `ac81f94f431f5d868d88e10fa784eb0da449264d` |
| **Runtime Config Fingerprint** | `4229274b582598bb869aafd4d0c139949559ebffab837546613be651ee60b2fa` |
| **실제 시작 시각 (Actual Start)** | `2026-09-15T10:26:33.652102Z` |
| **계획 종료 시각 (Planned Stop)** | `2026-09-16T17:00:00Z` |
| **감사 시점 벽시계 (Audit Time)** | `2026-09-17T00:30:00Z` (계획 종료 대비 +7.5시간 경과) |
| **대상 EC2 인스턴스** | `i-008bc503c1136349f` (ap-northeast-2) |

---

## 2. 터미널 상태 실측 사실 (Terminal Observations)

1. **EC2 및 SSM 상태**:
   - EC2 인스턴스는 `running` 상태 유지.
   - SSM Agent는 `Online` 상태(버전 3.3.4624.0) 유지.
   - IAM 최소 권한 원칙(Read-Only)에 따라 인스턴스 내부 원격 명령 실행(`ssm:SendCommand`)은 불허됨.
2. **S3 아카이브 실측 (전수 재귀 인벤토리 확인)**:
   - 전수 조사 대상 버킷: `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433`
   - V4 접두사: `market-data/temporary/aws-validation-30h-20260915-v4/`
   - 총 객체 수: **76개**
   - 총 용량: **534,594 바이트 (약 0.5 MB)**
   - 원시 데이터(RAW_MARKET_DATA) 객체 수: **0개**
   - 커버리지 증거(COVERAGE_EVIDENCE) 객체 수: **76개** (모두 `coverage/2026-09-15_10/` 단 1개 시간에 한정됨)
   - 최종 데이터 활동 시각: `2026-09-15T11:01:37Z`
   - 이후 약 37.5시간 동안 추가적인 S3 업로드, 하트비트, 종료 영수증, 또는 매니페스트 전혀 없음.

---

## 3. 최종 인프라 평가 (Final Infrastructure Audit)

| 분류 | 판정 | 근거 및 세부 내역 |
|---|---|---|
| **PROCESS** | **FAIL** | 계획된 30시간 수집 중 시작 1시간(`2026-09-15_10`) 이후 추가 수집/발행 중단. 자격 충족 시간 0/30시간. |
| **ARCHIVE** | **FAIL** | 원시 호가/체결 데이터(RAW JSONL/ZST)가 S3에 단 1건도 업로드되지 않음. |
| **DQ (Data Quality)** | **FAIL** | 규정된 2,280개 슬롯 중 0개 충족 (0 qualifying hours). |
| **EVIDENCE_CONTRACT**| **FAIL** | 최종 종료 매니페스트, 아카이브 영수증, 실행 요약 미생성. |
| **OVERALL** | **FAIL** | 인프라 수집 및 아카이브 계약 미충족. |

---

## 4. 데이터셋 연구 가용성 (Dataset Research Usability)

- **판정**: **`NOT_RESEARCH_USABLE`** (연구 활용 불가)
- **사유**:
  - 원시 호가, 체결, 틱 데이터가 전무하여 실증 연구, 백테스팅, 모델 검증에 필요한 입력값이 존재하지 않음.
  - 전향적 검증(Prospective Validation) 데이터셋으로서의 최소 요건(신뢰할 수 있는 연속 체결/호가 데이터)을 충족하지 못함.

---

## 5. 원인 분석 (Root Cause Forensic)

- 인스턴스 OS는 생존해 있었으나, 수집 프로세스 또는 로컬-S3 아카이브 파이프라인이 2026-09-15 11:01 UTC 시점에 중단되었거나 교착 상태에 빠진 것으로 판단됨.
- 로컬 임시 디렉토리에 데이터가 남아있을 가능성이 있으나 원격 실행/수정 권한이 차단된 안전 모델 하에서는 S3에 영구 보존된 데이터만이 유효한 감사 대상임.
- 절차적 원칙에 따라 시스템을 임의로 재시작하거나 임의 복구하지 않고 실패 사실을 그대로 확정 기록함.

---

## 6. 후속 연구 방향 전환 (Scientific Decision)

V4 데이터셋이 `NOT_RESEARCH_USABLE`로 확정됨에 따라:
1. V4에 대한 전향적 알파 검증은 수행하지 않음.
2. 검증 완료된 **V2 30시간 고해상도 데이터셋(`aws-validation-30h-20260912-6576f63`)**을 기반으로:
   - **Maker(수동 체결) 모델 확장 연구**: Taker 비용 극복 가능성 실증 (Base/Conservative)
   - **Cross-Exchange(Binance/Upbit) 인과적 지연 연구**: 타임스탬프 계약 수정 및 선행성 검증
3. 본 연구 결과는 **회고적 연구 / 방법론 개발(RETROSPECTIVE RESEARCH / METHOD DEVELOPMENT)**로 명확히 라벨링함.
