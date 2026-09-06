# Microstructure Research Preregistration V1 Audit (2026-09-05)

## 1. 개요 및 목적

본 감사는 사전 등록 문서 `docs/MICROSTRUCTURE_RESEARCH_PREREGISTRATION_V1.md` 및 동반 명세서 `research/preregistration/microstructure_v1.json`이 수립한 가설, 표본 분할(Discovery 24h, Validation 24h, Embargo 2h, Holdout 22h), 피처 수학적 정의의 학술적/통계적 타당성을 엄격히 비판 검토합니다.

**핵심 판정:**
> [!IMPORTANT]
> **72시간 Soak 수집 데이터셋은 통계적으로 유의미한 알파(Alpha) 가설을 확정 검증하기에 표본 다양성 및 독립성이 절대적으로 부족합니다.**
> 72H 데이터셋은 **"수집 인프라 정상성, 데이터 무결성(DQ), 오프라인 파이프라인 적격성 검증 데이터셋(Pipeline Qualification Dataset)"**으로 운용되어야 하며, 이를 알파 검증 완료의 근거로 사용해서는 안 됩니다.

---

## 2. 72시간 데이터셋의 통계적 한계점 상세 분석

### 1) 표본 독립성 결여 (Lack of Sample Independence)
- 고주파 마이크로스트럭처(호가/체결) 데이터는 밀리초~초 단위로 수백만 건 생성되지만, 강한 자기상관(Autocorrelation)과 체결 클러스터링(Vol Clustering)을 보입니다.
- 72시간 동안 수집된 500만 건의 이벤트는 "500만 개의 독립 관측치"가 아닙니다. 유효 독립 표본 크기($N_{eff}$)는 체결 클러스터와 호가 체류 시간(Order Book Resting Time)을 고려할 때 수백~수천 개 수준에 불과합니다.

### 2) 주중/주말 및 일중 계절성(Intraday Seasonality) 미포괄
- 72시간은 주 7일 중 3일(예: 토, 일, 월)만을 포함하므로 주중 기관 유동성 진입, 주말 리테일 거래 패턴의 교차 효과를 반영할 수 없습니다.
- 최소 1개월 이상의 다주(Multi-week) 데이터가 누적되지 않는 한, 특정 요일의 우연한 변동성을 체계적 엣지(Edge)로 오인할 위험이 극도로 높습니다.

### 3) 레짐(Regime) 및 변동성 다양성 부재
- 72시간 동안 시장이 횡보장(Chop) 또는 완만한 상승장 단일 국면에 머물 경우, 급락장(Flash Crash), 유동성 증발(Liquidity Void), 고변동성 뉴스 이벤트에 대한 전략 반응을 검증할 수 없습니다.

### 4) 시간 분할의 취약성 (24h Discovery / 24h Validation / 22h Holdout)
- 24시간 Discovery 후 바로 다음 날 24시간 Validation, 그리고 그 다음 날 22시간 Holdout으로 분할하는 구조는 동일한 거시적 시장 충격(Macro regime)이 3개 윈도우 전체에 연속적으로 잔존(Spillover)할 위험이 큽니다.
- 2시간의 Purged Embargo는 호가창 잔류 메모리를 씻어내기에는 충분하지만, 일 단위 추세나 거시 이벤트의 직렬 상관을 차단하지 못합니다.

---

## 3. 피처 정의(OFI, ATI, MPQI)의 수식상 결함

### 1) OFI (Order Flow Imbalance) 수식 결함
- V1 정의: $\text{OFI}_t = \Delta \text{BidSize}_t \cdot \mathbb{I}(\text{Bid}_t \ge \text{Bid}_{t-1}) - \Delta \text{AskSize}_t \cdot \mathbb{I}(\text{Ask}_t \le \text{Ask}_{t-1})$
- **결함**: Cont, Kukanov & Stoikov (2014)의 정석적인 호가 흐름 불균형 공식에 따르면, 최고 호가가 변동했을 때(Price Level Change) 단순히 호가 수량 차이($\Delta \text{Size}$)만 반영하는 것은 잘못되었습니다.
  - 최우선 매수 호가가 상승한 경우: 이전 호가 레벨 위의 새로운 레벨이 형성된 것이므로, 이전 잔량과 무관하게 신규 잔량 전체($q_{b,t}$)가 양의 유입입니다.
  - 최우선 매수 호가가 하락한 경우: 기존 최우선 레벨이 전량 체결되거나 취소된 것이므로, 이전 잔량($-q_{b,t-1}$)이 음의 유입입니다.
- **조치**: V1 수식(`ofi_v1`)은 역사적 기록으로 보존하고, Cont et al. (2014)의 엄밀한 호가 레벨 전이 수식을 `ofi_v2`로 정식 구현 및 개정안에 반영합니다.

### 2) ATI (Aggressive Trade Imbalance) 체결 방향성 의미론
- 거래소마다 체결 이벤트에 명시된 체결 방향(Aggressor Side)의 정의가 상이함:
  - Bithumb: `ask`(매도 체결 = Taker Sell), `bid`(매수 체결 = Taker Buy).
  - Binance: `m` (isBuyerMaker: true면 Taker Sell, false면 Taker Buy).
  - Upbit: `ask_bid` (ASK = 매수/매도 구분 규약 확인 필요).
- 범용적인 BUY/SELL 변환 레이어와 엄격한 거래소별 의미론 계약(Semantics Contract)이 선행되어야 함.

### 3) MPQI (Microprice & Queue Imbalance) 레벨 가중치 미지정
- L1~L5 복수 호가 레벨을 집계할 때 가중치 방식(거리 역수 가중, 선형 감쇠, 기하 감쇠)이 명시되지 않음.
- L1~L5 각각에 대한 명시적 깊이 가중치 벡터 규약 필요.
