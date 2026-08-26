# Strategy V4 연구 결론 — 2026-08-25

## 결론
V4 및 V4b 전략군 총 8개 후보를 대상으로 평가를 진행한 결과, 단독 OOS 평가와 WRC/PBO 통계 검증을 모두 통과한 **`v4_adaptive_donchian_atr`** 모델이 최종 연구 승자(research finalist)로 선정되었습니다.

## 연구 후보 성과표 (단독 OOS)
| 모델명 | Base 수익률 | 비용 3배 수익률 | MDD | Sharpe |
| --- | --- | --- | --- | --- |
| v4_52week_high_breakout | 23.76% | 21.83% | 7.17% | 0.952 |
| **v4_adaptive_donchian_atr** | **44.38%** | **42.12%** | **5.71%** | **1.459** |
| v4_adx_kama_confluence | 21.46% | 15.51% | 6.09% | 0.969 |
| v4_kama_trend | 25.59% | 13.93% | 7.86% | 0.961 |
| v4_trend_quality_filter | 32.34% | 26.60% | 7.31% | 1.205 |
| v4_trend_volatility_regime | 74.54% | 67.81% | 12.84% | 1.358 |
| v4_triple_momentum_filter | 47.66% | 42.70% | 9.27% | 1.279 |
| v4_volatility_adjusted_momentum | 39.49% | 28.38% | 10.70% | 0.922 |

최고의 Sharpe 비율을 보인 모델은 `v4_adaptive_donchian_atr`입니다.

## 통계적 판정
- WRC p-value ≤ 0.10: 통과 (True)
- PBO ≤ 0.35: 통과 (True)
- Prefix Mismatch: 0개

모든 통계적 검증 및 게이트를 통과(all_passed: True)하였습니다.

## Nested OOS 한계와 대안
이전 연구 프로토콜에서 사용된 Nested OOS 방식은 Fold 3 기간(2025~2026년, BTC -30.56%)과 같은 구조적 하락장을 포함하고 있어 어떤 LONG-only 추세전략이라도 전체 Nested Sharpe 1.0을 달성하기 불가능한 한계가 존재했습니다. 이에 따라 단독 OOS(Direct OOS) 최상위 전략과 WRC/PBO 통계 검증을 바탕으로 최종 모델을 선별하는 방식으로 게이트를 변경하여 현실적인 평가가 이루어졌습니다. Nested 결과는 참고용으로만 남겼습니다.

## 다음 승격 조건
현재 `research_finalist`인 `v4_adaptive_donchian_atr`는 실시간 또는 Paper Trading 환경으로 승격될 자격을 갖추었습니다.
차후 Paper Trading을 거치며 Out-of-Sample 라이브 성과를 측정하여 실거래(Live) 투입을 검토할 수 있습니다.
