# 미래 마이크로스트럭처 기술 특성 사양서 (Microstructure Feature Specifications)

> [!NOTE]
> 본 사양서는 향후 마이크로스트럭처 알파 모델링을 위한 순수 기술 명세서이며, 수익률 최적화나 전략 파라미터 튜닝을 포함하지 않는다. 모든 특성은 미래 정보 누수(Lookahead)가 없도록 엄격한 시계 기준을 정의한다.

---

## 1. Order-Flow Imbalance (OFI)

- **원시 필드 (Raw Fields):**
  - 최우선 매수/매도 호가 및 잔량: $b_t, q_t^b, a_t, q_t^a$
  - 이전 스냅샷 호가 및 잔량: $b_{t-1}, q_{t-1}^b, a_{t-1}, q_{t-1}^a$
- **수학적 정의 (Definition):**
  $$I_t = \Delta W_t^b - \Delta W_t^a$$
  where
  $$\Delta W_t^b = \begin{cases} q_t^b & \text{if } b_t > b_{t-1} \\ q_t^b - q_{t-1}^b & \text{if } b_t = b_{t-1} \\ 0 & \text{if } b_t < b_{t-1} \end{cases}$$
  $$\Delta W_t^a = \begin{cases} q_t^a & \text{if } a_t < a_{t-1} \\ q_t^a - q_{t-1}^a & \text{if } a_t = a_{t-1} \\ 0 & \text{if } a_t > a_{t-1} \end{cases}$$
- **시계 선택 (Clock Choice):**
  - 로컬 수신 단조 시계 (`monotonic_timestamp`) 기준 이벤트 인덱스 $t$.
- **Lookahead 편향 위험:**
  - 호가창 스냅샷 도착 시점 이전에 다음 호가 변동을 참조하지 않도록 strict lag-1 연산 적용.
- **결측 데이터 정책 (Missing Data Policy):**
  - WebSocket 재연결 직후 첫 번째 스냅샷은 이전 상태와의 차분을 계산하지 않고 $I_t = 0$ 처리.
- **정규화 (Normalization):**
  - 과거 5분 롤링 창의 스프레드 가중치 또는 평균 거래량으로 정규화.
- **체결 가정 (Execution Assumptions):**
  - 신호 발생 시점 기준 즉시 체결 불가능, 다음 틱의 호가창 깊이에 테이커 주문 진입.

---

## 2. Aggressive Trade Imbalance (ATI)

- **원시 필드:**
  - 체결 방향(`side`: BUY/SELL), 체결 수량(`qty`), 체결 가격(`price`)
- **수학적 정의:**
  $$ATI_{\Delta t} = \frac{\sum_{i \in \Delta t, \text{BUY}} V_i - \sum_{j \in \Delta t, \text{SELL}} V_j}{\sum_{k \in \Delta t} V_k + \epsilon}$$
- **시계 선택:**
  - 시간 윈도우 기반: 로컬 벽시계 (`receive_timestamp`), 틱 기반: 이벤트 단조 시계.
- **Lookahead 편향 위험:**
  - 집계 구간의 마지막 체결 이벤트의 수신 완료 시각 이후에만 신호 방출.
- **결측 데이터 정책:**
  - 체결이 없는 무거래 구간은 $ATI = 0$.
- **정규화:**
  - $[-1.0, +1.0]$ 유계 구간.

---

## 3. Microprice (미세가격)

- **원시 필드:**
  - $b_t, q_t^b, a_t, q_t^a$
- **수학적 정의:**
  $$P_t^{\text{micro}} = \frac{q_t^b \cdot a_t + q_t^a \cdot b_t}{q_t^b + q_t^a}$$
- **시계 선택:**
  - 단조 시계 기준 최신 호가 스냅샷.
- **Lookahead 편향 위험:**
  - 현재 시점의 상위 1호가만 사용하므로 누수 위험 없음.
- **정규화:**
  - 미드프라이스 $M_t = (a_t + b_t)/2$ 대비 스프레드 단위 베이시스: $\frac{P_t^{\text{micro}} - M_t}{(a_t - b_t)}$.

---

## 4. Queue Pressure (호가 큐 압력)

- **원시 필드:**
  - 5단계 호가 깊이: $\{b_t^k, q_t^{b,k}, a_t^k, q_t^{a,k}\}_{k=1}^5$
- **수학적 정의:**
  $$QP_t = \frac{\sum_{k=1}^K w_k \cdot q_t^{b,k} - \sum_{k=1}^K w_k \cdot q_t^{a,k}}{\sum_{k=1}^K w_k (q_t^{b,k} + q_t^{a,k})}, \quad w_k = \frac{1}{k}$$
- **시계 선택:**
  - 단조 시계 기준.
- **정규화:**
  - $[-1.0, +1.0]$.

---

## 5. Bid-Ask Spread Dynamics

- **원시 필드:**
  - $a_t, b_t$
- **수학적 정의:**
  $$S_t^{\text{abs}} = a_t - b_t, \quad S_t^{\text{rel}} = \frac{a_t - b_t}{M_t}$$
- **정규화:**
  - 24시간 롤링 분위수(Percentile Rank) 변환.

---

## 6. Volume Shock (순간 거래량 충격)

- **원시 필드:**
  - 롤링 구간 체결량 $\text{Vol}_{\Delta t}$
- **수학적 정의:**
  $$Z_{\text{vol}} = \frac{\text{Vol}_{\Delta t} - \mu_{\text{vol}}(t)}{\sigma_{\text{vol}}(t) + \epsilon}$$
- **Lookahead 방지:**
  - $\mu, \sigma$ 계산 시 반드시 과거 롤링 윈도우만 사용(No centered rolling window).

---

## 7. Cross-Exchange Lead/Lag (거래소 간 선도-지행 지표)

- **원시 필드:**
  - 바이낸스 미드프라이스 $M_t^{\text{BN}}$, 빗썸 미드프라이스 $M_t^{\text{BT}}$
- **수학적 정의:**
  - 바이낸스 선도 수익률과 빗썸 지행 수익률의 교차 상관(Cross-correlation) 지표.
- **시계 선택:**
  - 반드시 **로컬 단조 수신 시계(Local Monotonic Clock)** 기준 정렬 (거래소별 서버 시계 불일치 방지).

---

## 8. Liquidity Regime (유동성 체제 분류)

- **원시 필드:**
  - 호가창 총 잔량, 스프레드, 체결 빈도
- **수학적 정의:**
  - K-Means 또는 가우시안 혼합 모델(GMM) 기반 3단계 체제:
    1. 고유동성 안정 체제 (Tight spread, deep book)
    2. 유동성 고갈 체제 (Wide spread, shallow book)
    3. 체결 충격 체제 (High volume, high volatility)
