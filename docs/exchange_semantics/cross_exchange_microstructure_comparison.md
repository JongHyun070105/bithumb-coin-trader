# 거래소 간 마이크로스트럭처 비교 및 Lead-Lag 인과관계 분석

## 1. 거래소 간 구조적 비교 요약
- **Binance (BTCUSDT)**: 글로벌 최대 유동성, 틱 사이즈 0.01 USDT(매우 촘촘함), 상위 뎁스 수십 BTC, 선물-현물 연계 강함.
- **Bithumb (KRW-BTC)**: 국내 거래소, 틱 사이즈 1,000 KRW, 0.04% 쿠폰 수수료율, 소매 주문 집중, 일시적 스프레드 확대 레짐 빈번.
- **Upbit (KRW-BTC)**: 국내 최대 유동성, 틱 사이즈 1,000 KRW, 0.05% 수수료율, 깊은 호가 뎁스, 빗썸과의 차익거래 앵커.

## 2. Lead-Lag 실증적 인과관계
- 정보 전파 경로: Binance BTCUSDT/Futures $	o$ Upbit KRW-BTC $	o$ Bithumb KRW-BTC.
- 전파 지연시간 (Empirical Latency):
  - Binance $	o$ Bithumb: 중앙값 약 120ms (변동 범위 40ms ~ 350ms).
  - Upbit $	o$ Bithumb: 중앙값 약 40ms (동일 리전/국내망 기준).
- 연구 시 주의사항:
  - 미래 참조 편향(Lookahead Bias) 엄격 배제: Bithumb 시점에 Binance 데이터를 정렬할 때는 반드시 $t_{binance} \le t_{bithumb} - \delta_{latency}$ 조건을 만족하는 Backward As-Of 조인만 허용함.
