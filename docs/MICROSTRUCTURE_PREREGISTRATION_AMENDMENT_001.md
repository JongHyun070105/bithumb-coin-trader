# Microstructure Research Preregistration Amendment 001 (2026-09-05)

**Amendment ID:** `prereg-amendment-20260905-001`  
**Base Registration:** `prereg-microstructure-20260905-v1` (`docs/MICROSTRUCTURE_RESEARCH_PREREGISTRATION_V1.md`)  
**Status:** `ACTIVE_AMENDMENT`  
**Software Commit:** `codex/72h-offline-phase2-20260905`  

---

## 1. 개정 배경 및 목적

`docs/MICROSTRUCTURE_PREREGISTRATION_V1_AUDIT.md`의 비판적 검토 결과, 초기 V1 명세가 72시간 연속 데이터셋을 실거래 알파(Alpha) 검증용 데이터셋으로 과도하게 상정하고 있음이 확인되었습니다.
고주파 금융 시계열의 높은 자기상관과 일중/요일별 계절성, 레짐 단일성을 감안할 때 72시간은 통계적 유의성을 확정하기에 부적합합니다.

이에 따라 본 수정안(Amendment 001)은 원본 V1을 삭제하지 않고 영구 보존하면서, 72H 데이터셋의 역할을 재정의하고 수식 및 프로토콜을 보완합니다.

---

## 2. 72H Soak 데이터셋의 공식 역할 재분류

```
72H SOAK DATASET ROLE:
PIPELINE INTEGRATION & DATA QUALITY (DQ) QUALIFICATION DATASET
(NOT DEFINITIVE ALPHA VALIDATION DATASET)
```

- **허용 용도:**
  1. 원격 S3 아카이브 무결성 및 zstd 압축/복원 오프라인 재현성 검증.
  2. 거래소별(Bithumb, Binance, Upbit) 시계열 정합성, 타임스탬프 왜곡, 갭(Gap) 탐지.
  3. 결정론적 리플레이 엔진, 인과적 피처 파이프라인, 테이커 체결 시뮬레이터, 페이퍼 엔진 통합 배관(Plumbing) 테스트.
- **금지 용도:**
  1. 72H 홀드아웃 결과만으로 전략을 실전(Live) 배포 가능 상태로 승인하는 행위.
  2. 72H 데이터에서 발견된 미세 상관관계를 일반화된 시장 엣지(Edge)로 홍보하는 행위.

---

## 3. 피처 수식 개정 및 버전 분리 (OFI v2)

### 1) OFI Version 2: Cont, Kukanov & Stoikov (2014) 표준 채택
기존 V1 수식(`ofi_v1`)의 가격 레벨 점프 미반영 문제를 해결하기 위해, 호가 변화($e_t$)를 정석적으로 정식화한 `ofi_v2`를 정의합니다:

최우선 매수호가 유입량 $I_{b, t}$:
$$
I_{b, t} = \begin{cases}
q_{b, t} & \text{if } P_{b, t} > P_{b, t-1} \\
q_{b, t} - q_{b, t-1} & \text{if } P_{b, t} = P_{b, t-1} \\
-q_{b, t-1} & \text{if } P_{b, t} < P_{b, t-1}
\end{cases}
$$

최우선 매도호가 유출량 $I_{a, t}$:
$$
I_{a, t} = \begin{cases}
-q_{a, t} & \text{if } P_{a, t} < P_{a, t-1} \\
-(q_{a, t} - q_{a, t-1}) & \text{if } P_{a, t} = P_{a, t-1} \\
q_{a, t-1} & \text{if } P_{a, t} > P_{a, t-1}
\end{cases}
$$

순 호가 흐름 불균형 $\text{OFI}_{v2, t} = I_{b, t} + I_{a, t}$.

### 2) 피처 식별자 버전 명시
- `ofi_v1`: V1의 단순 부호 곱셈 수식 (역사적 호환성 유지).
- `ofi_v2`: Cont et al. (2014) 정석 수식 (권장 표준).

---

## 4. 향후 확정적 알파 연구를 위한 필수 데이터셋 요건 프로토콜

향후 페이퍼 트레이딩 또는 실전 배포를 목적으로 하는 공식 알파 연구는 다음 요건을 충족하는 데이터셋에서만 개시할 수 있습니다:

1. **최소 관측 일수:** 결측 없는 최소 **30일 이상** (권장: 90일, 최소 4회 이상의 주말/주중 사이클 포함).
2. **다양한 시장 레짐 포괄:**
   - 저변동성 박스권 (Low Volatility Range)
   - 급격한 변동성 확장 국면 (High Volatility Expansion)
   - 강한 일방향 추세장 (Trending Bull / Bear)
3. **독립 체결 기회 (Independent Trade Opportunities):**
   - 시계열 블록 상관성을 배제한 최소 **500회 이상**의 독립적 왕복(Round-Trip) 체결 표본.
4. **유효 표본 수 사전 계산:**
   - 이벤트 수의 단순 합산이 아니라, 시계열 유효 자유도($N_{eff}$)와 전력 분석(Power Calculation)을 통한 최소 유의 수준 사전 확정.
