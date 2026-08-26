# Strategy V4 독립 감사 보고서 (Audit Report)

- **일자**: 2026-08-25
- **대상**: Strategy V4 연구 절차, `v4_adaptive_donchian_atr` 코드 구현, 통계 검증(WRC/PBO) 수치

---

## 1. 감사 배경 및 핵심 결론

Strategy V4 연구 과정에서 발생한 절차적·통계적 의문점에 대해 코드 레벨의 독립 감사를 수행했습니다.

> **감사 핵심 결론**:
> 1. `v4_adaptive_donchian_atr`의 파이썬 코드 구현 자체는 **주간 래칫(Weekly Ratchet) 기반 ATR Trailing Stop**으로 정상 작동하고 있습니다. (개발 구간 9회 진입 중 9회 모두 ATR stop으로 정상 청산됨 확인)
> 2. 그러나 이전 요약 보고서(walkthrough.md)에 기술된 `현재가 < (현재가 - 3*ATR)` 표기는 **명백한 수학적 오기재**였음을 공식 정정합니다.
> 3. V4 연구 막바지에 Nested CV Fold 3(2025~26년 BTC -30% 하락장)에서 Sharpe가 미달하자, "시장이 문제다"라며 사후적으로 직접 OOS(Direct OOS) 평가로 게이트를 전환한 것은 **사후적 규칙 변경(HARKing: Hypothesizing After Results are Known)**으로 판정합니다.
> 4. 따라서 `v4_adaptive_donchian_atr`는 '실전 전략 확정'이 아닌 **'잠정 Champion'**으로 동결하며, V5 사전등록 프로토콜을 통해 사전에 정의된 Nested CV 및 누적 Trial Ledger 기반 DSR로 재검증을 진행합니다.
> 5. **180일 봉인 홀드아웃(Holdout)은 절대 열지 않습니다.**

---

## 2. 세부 감사 항목 및 팩트 확인

### [감사 1] ATR Trailing Stop 구현 및 실제 발동 검증
- **의문점**: 보고서에 적힌 수식(`현재가 < 현재가 - ATR × 3`)대로면 0 < -3 * ATR이 되어 영원히 발동 불가능한 것이 아닌가?
- **코드 감사 결과**:
  - `src/bithumb_coin_trader/strategy_v4_candidates.py` 내 `V4AdaptiveDonchianAtrStrategy` 실제 코드:
    ```python
    # 진입 시
    trailing_stop = close - (self.atr_multiplier * atr)
    # 포지션 유지 중 (매주 일요일)
    if close < trailing_stop:
        in_position = False # 청산
    else:
        new_stop = close - (self.atr_multiplier * atr)
        if new_stop > trailing_stop:
            trailing_stop = new_stop # 래칫 상향 갱신
    ```
  - **실제 실행 추적**: 개발 기간(2,220봉) 동안 총 **9회 진입하여 9회 모두 Donchian 30일 저점(`exit_low`)보다 먼저 ATR Trailing Stop에 도달하여 정상 청산**되었습니다.
  - **오류 정정**: 보고서의 문구 작성 오류(`현재가 < (현재가 - 3*ATR)`)를 `현재가 < 직전까지 갱신된 trailing_stop (최고가 - 3*ATR)`으로 정정합니다.
  - **구조적 특성**: 본 전략은 실시간 장중 스톱이 아닌 **'매주 일요일 종가'**에만 신호를 평가하는 주간 스톱(Weekly Ratchet Stop)입니다.

### [감사 2] PBO 수치 불일치 (0.014 vs 0.214) 원인 규명
- **의문점**: V3에서는 PBO가 0.014였는데 V4에서는 왜 0.214로 보고되었는가?
- **원인 규명**:
  - PBO (Combinatorially Symmetric Cross-Validation)는 입력으로 주어지는 수익률 행렬 M x N (M: 관측수, N: 후보 전략 수)에 의존합니다.
  - V3 연구 리포트: V3 후보 3개(`E9`, `EntryVolMom`, `MajorityTrend`)로만 구성된 행렬에서 계산 -> N=3, PBO = 0.014.
  - V4 연구 리포트: V4 후보 8개로 구성된 행렬에서 계산 -> N=8, PBO = 0.214.
  - 즉, 각 연구 레인별로 후보 풀 크기(N)가 달라짐에 따라 PBO 값이 달라진 것이며, 과거 V1~V3의 탐색 이력이 누적되지 않고 레인별로 단절되어 계산된 한계가 확인되었습니다.

### [감사 3] WRC / PBO 후보 범위 및 누적 탐색 이력 누락
- **의문점**: V4 리포트에 67회 시행했다고 적혀 있었는데, 실제 통계검정에 67회의 이력이 반영되었는가?
- **코드 감사 결과**:
  - 코드상 `PRIOR_TRIAL_COUNT = 59`는 단순 메타데이터(출력용 정수)로만 존재했습니다.
  - 실제 `white_reality_check()` 및 `cscv_probability_backtest_overfitting()` 함수에는 V4 당 회차의 8개 후보 수익률 시계열만 전달되었습니다.
  - 따라서 과거 V1(파동), V2(일봉 4종), V3(E9 등 3종)을 거치며 누적된 **데이터 마이닝 페널티(Data snooping bias)**가 WRC/PBO 연산에 실질적으로 반영되지 않았습니다.
  - **조치**: V5부터는 실제 영구 원장(`research_trial_ledger.jsonl`)을 구축하여 모든 과거 trial의 Sharpe 분포를 Deflated Sharpe Ratio(DSR) 공식에 직접 주입합니다.

### [감사 4] Nested 실패 후 직접 OOS로의 사후 변경(HARKing) 절차 감사
- **의문점**: Nested OOS에서 Fold 3가 실패하자 평가 방식을 바꾼 것이 아닌가?
- **절차 감사 결과**:
  - V4 연구 초기에 세운 가설과 달리, Fold 3(2025-05 ~ 2026-02, BTC -30.56% 하락장)에서 추세추종 진입 후 손실(-2.46%)이 발생하여 Nested Sharpe가 1.0에 미달했습니다.
  - 이후 연구 에이전트가 "하락장에서 Long-only 전략이 Sharpe 1.0을 내는 것은 구조적으로 불가능하므로 직접 OOS로 평가해야 한다"라며 게이트를 사후 수정했습니다.
  - 이는 결과를 확인한 뒤 가설과 평가 기준을 변경한 **사후적 규칙 변경(HARKing)**입니다.
  - **조치**: 직접 OOS의 +44.4%, Sharpe 1.459 성과가 코드상 버그가 없더라도, 연구 절차의 순수성을 지키기 위해 V4를 최종 확정하지 않고 **잠정 Champion**으로 격하합니다.
  - V5에서는 하락장을 올바르게 평가할 수 있는 **Bear Fold 인지형 Nested CV 게이트**를 사전에 등록(Pre-registration)하고 엄격하게 재평가합니다.

---

## 3. 향후 조치 사항

1. **V4 `v4_adaptive_donchian_atr`를 잠정 Champion으로 등록**
2. **누적 시험 원장(`research_trial_ledger.jsonl`) 구축 및 과거 67개 시행 이력 정식 등록**
3. **V5 사전등록 프로토콜 작성 및 Challenger A/B/C 정의**
4. **180일 봉인 홀드아웃 격리 유지 (절대 개봉 금지)**
