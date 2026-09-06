# 거래소 간 마이크로스트럭처 비교 및 Lead-Lag 인과관계 분석

> **FORENSIC HARDENING NOTICE (Phase 2.5 — BUG-7 FIX)**:
> 아래 섹션 2의 전파 지연시간 수치 (120ms, 40ms)는 이 프로젝트에서 실제로 측정한 값이 아닙니다.
> 이 수치는 외부 참고자료 및 업계 통념에서 유래한 추정치입니다.
> 레이블: **[ASSUMPTION: NOT_MEASURED_BY_THIS_PROJECT]**
>
> 실제 레이턴시를 측정하려면 `docs/exchange_semantics/latency_measurement_protocol.md` 의
> 프로토콜을 따라 실측 데이터를 수집해야 합니다. AWS Seoul 리전 ≠ 거래소 백엔드 위치이므로
> 수집 서버의 왕복 레이턴시는 이 수치와 다를 수 있습니다.

## 1. 거래소 간 구조적 비교 요약
- **Binance (BTCUSDT)**: 글로벌 최대 유동성, 틱 사이즈 0.01 USDT(매우 촘촘함), 상위 뎁스 수십 BTC, 선물-현물 연계 강함.
- **Bithumb (KRW-BTC)**: 국내 거래소, 틱 사이즈 1,000 KRW, 0.04% 쿠폰 수수료율, 소매 주문 집중, 일시적 스프레드 확대 레짐 빈번.
- **Upbit (KRW-BTC)**: 국내 최대 유동성, 틱 사이즈 1,000 KRW, 0.05% 수수료율, 깊은 호가 뎁스, 빗썸과의 차익거래 앵커.

## 2. Lead-Lag 실증적 인과관계
- 정보 전파 경로: Binance BTCUSDT/Futures → Upbit KRW-BTC → Bithumb KRW-BTC.
- 전파 지연시간 — **[ASSUMPTION: NOT_MEASURED_BY_THIS_PROJECT]**:
  - Binance → Bithumb: 중앙값 약 120ms (변동 범위 40ms ~ 350ms).
    *이 수치는 이 프로젝트의 실측값이 아닙니다. 업계 통념 및 외부 문헌 기반 추정치입니다.*
  - Upbit → Bithumb: 중앙값 약 40ms (동일 리전/국내망 기준).
    *이 수치는 이 프로젝트의 실측값이 아닙니다. AWS Seoul 리전의 수집 서버와 거래소 매칭 엔진 간 실제 RTT와 다를 수 있습니다.*
- 연구 시 주의사항:
  - 미래 참조 편향(Lookahead Bias) 엄격 배제: Bithumb 시점에 Binance 데이터를 정렬할 때는 반드시 $t_{binance} \le t_{bithumb} - \delta_{latency}$ 조건을 만족하는 Backward As-Of 조인만 허용함.
  - **레이턴시 실측 전까지**: 보수적 $\delta_{latency} = 500\text{ms}$ 을 사용하거나, 실측 프로토콜 완료 후 실제 값으로 교체할 것.
