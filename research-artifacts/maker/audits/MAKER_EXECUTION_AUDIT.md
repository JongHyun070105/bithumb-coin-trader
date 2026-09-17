# Maker 실행 시뮬레이터 감사 보고서 (Maker Execution Simulator Audit)

**감사 일시**: 2026-09-17 09:32:00 KST  
**대상 모듈**: `src/bithumb_coin_trader/maker_simulator.py`  
**감사관**: Quantitative Microstructure Specialist / Autonomous Director  
**감사 결론**: **REVISION_REQUIRED (핵심 로직 수정 및 양방향 확장 필요)**

---

## 1. 개요 및 목적

V2 연구의 핵심 교훈은 다음과 같습니다:
- **신호의 유의미한 예측력 존재**: 호가 불균형(H1)의 IC가 30초에서 최대 0.333(XRP), 0.294(ETH)에 달함.
- **Taker 슬리피지/스프레드 비용의 파괴적 효과**: 예측 엣지(1~3 bps) 대비 호가 스프레드를 건너는 Taker 체결 비용(1.9 ~ 8.8 bps)이 체계적으로 커서 순손익이 전면 음수(-1.94 ~ -8.78 bps)로 귀결됨.

이에 따라 **수동 주문(Maker/Passive Order)을 통해 스프레드를 건너지 않고 포착할 경우, 예측 엣지가 실질적인 경제적 수익(Net PnL)으로 전환될 수 있는가?**가 본 연구의 핵심 질문입니다.

이를 객관적으로 검증하기 위해 기존 `maker_simulator.py`의 구조적 무결성을 엄격히 감사하였습니다.

---

## 2. 세부 항목별 감사 결과

### 2.1 대기열(Queue) 로직 및 수치적 차원 결함 (Critical Bug)
- **현상**: `_check_orderbook_fill_buy()`의 CONSERVATIVE 분기에서:
  ```python
  queue_ahead = available * self.assumptions.queue_multiplier
  fillable = max(0, remaining_qty - queue_ahead)
  ```
- **결함 분석**:
  - `remaining_qty`는 **우리 주문 수량(Order Quantity)**입니다.
  - `queue_ahead`는 우리 주문 앞에 서 있는 **타인의 대기 수량(Other Traders' Queue Volume)**입니다.
  - 우리 수량에서 타인의 대기 수량을 빼는 것은 명백한 차원적 결함(Dimensional Inconsistency)입니다.
  - **올바른 메커니즘**: 우리 주문이 체결되려면 유입되는 공격적 체결량(Aggressive Trade Volume) 또는 반대편 호가의 거래량이 `queue_ahead`를 초과하여 소진한 후, 초과분만큼 우리 주문이 체결되어야 합니다.

### 2.2 주문 방향 편향 (Lack of SELL Support)
- **현상**: `evaluate_passive_buy()`만 존재하며, 숏/매도 신호에 대응하는 `evaluate_passive_sell()` 또는 일반화된 주문 처리기가 전무함.
- **개선**: BUY와 SELL 대칭 구조를 지원하는 통합 평가 파이프라인 구축 필요.

### 2.3 지연 시간(Latency) 미반영
- **현상**: `MakerAssumptions`에 `latency_ms = 100.0`이 정의되어 있으나, 루프에서 `ts < placement_ts + latency_ns` 구간의 이벤트를 건너뛰지 않고 즉시 체결 평가함.
- **결함**: 주문이 거래소 엔진에 도달하기 전에 발생한 이벤트로 인해 즉시 체결되는 룩어헤드/비인과적 체결 위험 존재.
- **개선**: `effective_placement_ts = placement_ts + latency_ns` 이전 이벤트는 체결 불가 처리.

### 2.4 청산(Exit) 시나리오 미구현
- **현상**: 주문이 진입(Entry)된 후 포지션 종료(Exit) 및 왕복 손익(Round-trip PnL) 계산 로직이 없음. 단지 진입 시점의 중간가 대비 슬리피지만 계산함.
- **개선**:
  - **Variant A (Passive Entry → Taker Exit)**: 지정가 진입 성공 후, 지정된 보유 기간(Horizon) 경과 시 시장가로 탈출.
  - **Variant B (Passive Entry → Passive Exit)**: 지정가 진입 성공 후, 반대편 호가에 지정가 청산 주문 제출 (취소 시간 존재).
  - **Variant C (Passive Entry → Timed Taker Unwind)**: 일정 시간 내 수동 청산 실패 시 강제 시장가 청산(Forced Exit).

### 2.5 체결 모델 3단계 정의 (Fill Semantics)
- **OPTIMISTIC**: 호가가 지정가에 터치만 해도 즉시 전액 체결 (현실성 없음, 상한선 검증용).
- **BASE**: 공격적 반대 방향 체결이 지정가 가격에서 발생하면 체결량 한도 내에서 체결.
- **CONSERVATIVE**: 주문 제출 시점의 동일 호가 잔량을 `queue_ahead`로 설정하고, 누적 공격적 체결량이 이를 초과할 때만 초과 체결.

---

## 3. 골든 테스트 요구사항 정의

모듈 수정 후 다음 11가지 결정론적 골든 테스트를 필수 통과해야 함:
1. `NO_FILL`: 체결 조건 미달 시 미체결.
2. `TOUCH_NO_FILL`: 가격 터치만 발생하고 큐 미소진 시 Conservative에서 미체결.
3. `QUEUE_NOT_CLEARED`: 공격적 체결량이 큐 대기 수량 이하인 경우 체결 없음.
4. `PARTIAL_FILL`: 큐를 초과한 체결량이 우리 주문 수량보다 적은 경우 부분 체결.
5. `FULL_FILL`: 큐를 초과한 체결량이 우리 주문 수량을 완전히 채운 경우 전액 체결.
6. `CANCELLED`: 체결 없이 취소 시각 도달 시 정상 취소.
7. `STALE`: 호가 만료/지연 시간 도달 시 만료.
8. `ADVERSE_SELECTION`: 체결 후 불리한 방향으로 가격이 급변하는 역선택 측정.
9. `PASSIVE_ENTRY_TAKER_EXIT`: 수동 진입 후 만기 시 시장가 청산 및 정확한 Net bps 계산.
10. `PASSIVE_ENTRY_PASSIVE_EXIT`: 수동 진입 후 반대 호가 수동 청산 및 Net bps 계산.
11. `FORCED_EXIT`: 일정 시간 미체결 시 손실 제한 강제 청산.

---

## 4. 감사 결론 및 승인

- 기존 `maker_simulator.py`는 단방향(BUY) 및 큐 로직 결함으로 인해 연구에 직접 사용 불가.
- 골든 테스트를 포함하여 상기 결함을 전면 수정한 견고한 `maker_simulator.py`로 리팩토링 진행을 승인함.
