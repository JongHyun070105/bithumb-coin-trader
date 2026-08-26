# Strategy V3 사전등록 — 2026-08-25

이 문서는 Strategy V3 개발 수익률을 계산하기 전에 후보와 평가 규칙을 고정한다. 결과를 본 뒤 정의·기간·임계값을 바꾸면 별도 신규 trial이다.

## 데이터 경계

- 거래시장: `KRW-BTC`, 현물 `LONG/FLAT`만 허용
- 입력: 공식 빗썸 KST 일봉 2,400개
- V3 개발 가능 구간: 앞 2,220개만
- Strategy V2의 마지막 180개 봉인 구간: V3 코드와 validator에 전달 금지
- 30분봉의 이전 4,000봉 holdout: 이미 소진·무효화됐으므로 승격 증거 재사용 금지

## Trial 57 — E9 Donchian ensemble

- 기간: `5, 10, 20, 30, 60, 90, 150, 250, 360`일
- 각 모델 진입: 완결 일봉 종가가 해당 기간의 Donchian 상단과 같을 때
- 각 모델 청산: 완결 종가가 전일에 확정된 비하향 trailing stop 이하
- 신규 trailing stop: 진입일 Donchian 중단
- 이후 stop: `max(previous_stop, current_Donchian_mid)`로 다음 날부터 적용
- 각 활성 모델 비중: `min(1.0, 0.25 / annualized_volatility_90d) / 9`
- 합산 비중: 0~100%, 레버리지 금지
- 모델 상태 수가 바뀌면 다음 일봉 시가에 즉시 목표 비중 리밸런싱
- 상태가 같고 변동성 때문에만 목표가 바뀌면 직전 목표와 20%p 이상 차이날 때만 리밸런싱
- 9개 모델을 따로 주문하지 않고 합산 목표와 현재 보유비중의 순차이만 한 건으로 처리

직접 근거: Zarattini, Pagani, Barbon, *Catching Crypto Trends*의 9기간 Donchian·90일 변동성·25% 목표 구조. 원문의 모델별 200% 상한은 빗썸 현물에 맞춰 100%로 축소한다.

## Trial 58 — entry-vol absolute momentum

- 기존 주간 절대모멘텀 126/63의 진입·청산 신호를 그대로 사용
- 진입 비중만 `min(0.50, 0.20 / annualized_volatility_28d)`
- 비중은 신규 진입 시 한 번 고정하고 보유 중 변동성 리밸런싱 없음
- 완결 KST 일요일에서만 상태 변경, 다음 일봉 시가 체결

## Trial 59 — frozen 2-of-3 majority

- 구성: 기존 동결 절대모멘텀 126/63, SMA50/200, Donchian90/30
- 완결 KST 일요일에서 세 상태 중 둘 이상 LONG이면 목표 30%, 아니면 0%
- 다음 일봉 시가 체결
- 구성 모델·파라미터를 변경하지 않음

## 공통 체결·회계

- 초기 연구 기준자본: 100,000원
- 현금 예비금: 5,000원
- 최소 주문: 5,000원
- 주문 1회 최대: 60,000원
- 기본 편도 비용: 수수료 25bp + 슬리피지 5bp
- 스트레스: 기본 비용의 2배·3배
- 신호는 닫힌 봉에서만 만들고 다음 봉 시가에서 목표 비중까지 부분 매수·매도
- 최소 주문 미만 목표 차이는 이월하고 허위 체결하지 않음
- 보유 중 자산곡선은 즉시 매도 시 슬리피지·수수료를 차감한 청산가치
- 최종 강제청산은 수익률에는 반영하되 정상 청산 수에 포함하지 않음

## Nested 개발 평가

- outer initial train: 1,320일
- outer test: 300일 × 3개
- 각 outer 선택은 해당 outer train의 마지막 600일만 inner evidence로 사용
- inner evidence는 200일 × 3개 fold로 나누어 보고
- 후보가 inner 기본비용과 3배비용에서 모두 양수일 때만 선택 가능
- 선택점수: `3배비용 수익률 / max(기본 최대낙폭, 1e-9)`
- 통과 후보가 없으면 cash
- 선택된 후보의 outer target만 하나의 연속 OOS 목표비중 시퀀스에 기록
- outer 경계에서 강제청산하지 않고 새 목표가 달라질 때만 거래
- outer test를 바꿔도 해당 fold 선택 결과가 바뀌면 validator 실패

## V3 finalist gate

다음을 모두 만족해야 개발 finalist가 될 수 있다.

1. prefix/lookahead mismatch 0
2. nested outer OOS 기본·2배·3배 수익률 모두 양수
3. 최대 낙폭 10% 이하
4. Sharpe 1.166608 이상
5. 3개 outer fold 모두 양수
6. 비용 증가 시 최종자산 비증가
7. 주문·현금·기초자산 회계 불일치 0
8. White Reality Check `p <= 0.05`
9. PBO `<= 0.25`
10. 과거 trial 수익률 원장이 없으므로 DSR은 `unavailable`, 따라서 자동 승격 불가

결과와 관계없이 `paper_or_live_strategy_changed=false`, `can_promote=false`다. 실제 승격 판단은 코드 동결 이후 prospective shadow evidence가 필요하다.
