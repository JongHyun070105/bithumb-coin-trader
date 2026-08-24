# KRW-BTC 고승률 후보 연구 — 2026-08-24

## 결론

37개 후보(현금 통제 1, 기존 16, 추세 4, 평균회귀 5, 변동성 4, 온라인 메타 3, 세션·VWAP 4) 중 사전 정의한 개발 게이트를 모두 통과한 전략은 없었습니다. 따라서 연구 결론은 다음과 같습니다.

- 선택 후보: `cash`
- 역사적 목표 달성: `false`
- 봉인 홀드아웃 개방: `false`
- 페이퍼 또는 라이브 전략 변경: 없음
- 자동 승격: 금지
- 운영 경계: 데몬의 감시·알림은 유지하고 신규 매수는 잠금

한 거래에서 100% 승률을 기록한 후보도 있었지만 표본 부족으로 탈락했습니다. 높은 과거 승률을 만들기 위해 같은 데이터에서 파라미터를 계속 조정하는 대신, 미사용 홀드아웃을 보존하고 현금 대기를 선택했습니다.

여기서 `검증 통과`와 `전략 통과`는 서로 다릅니다. Validator는 해시로 고정한 공통 `Backtester`로 체결을 재생하되, 거래 지표·게이트·순위는 보고서 생성기와 별도 코드로 다시 계산합니다. 데이터·후보·실행 엔진 소스 해시, 결과 mirror 일치, 라이브 신규 진입 잠금을 검사해 `passed=true`, `issues=[]`를 기록했습니다. 그러나 전략은 하나도 통과하지 않았습니다.

## 데이터와 검증 프로토콜

- 시장: 빗썸 `KRW-BTC` 현물 30분봉
- 데이터: 완료된 공개 OHLCV 45,000개
- 기간: 2024-01-27 14:30 KST ~ 2026-08-24 20:00 KST
- 데이터 SHA-256: `aaeae174f944b1ca86ad4af6ec059a1eb86c8e2877d99af0cdfd3e3d4746018d`
- 결측 간격: 18개. 결측 뒤 첫 관측 시가에서 강제 `FLAT`
- 개발 구간: 41,000개, 초기 학습 17,000개 이후 4,000개씩 6개 expanding OOS fold
- 봉인 홀드아웃: 마지막 4,000개. 개발 게이트를 통과한 후보가 없어 열지 않음
- 체결: 완료된 30분봉에서 신호를 만들고 다음 봉 시가에 체결
- 기본 비용: 체결당 수수료 0.25% + 슬리피지 5bp
- 스트레스 비용: 체결당 수수료 0.50% + 슬리피지 10bp
- 공매도·피라미딩: 금지
- KST 일일 신규 진입: 운영 데몬과 동일하게 최대 4회

개발 통과 조건은 승률 70% 이상, 정상 청산 거래 30건 이상, 기본·2배 비용 수익률 양수, profit factor 1 초과, 최대 낙폭 15% 이하, 6개 중 4개 이상 수익 fold, 승률 Wilson 95% 하한 50% 이상을 동시에 만족하는 것입니다.

기본 runner는 개발 구간만 후보에게 전달합니다. 개발 통과 후보가 생겨 명시적으로 holdout을 여는 경우에는 데이터·후보·프로토콜 해시와 finalist 목록을 담은 `holdout-ledger.json`을 평가 전에 원자적으로 생성합니다. `opening`을 포함해 원장이 이미 존재하면 모든 재실행을 거부하므로 동일 봉인 구간을 반복 조회할 수 없습니다.

## 후보군과 결과

후보는 기존 전략과 추세·모멘텀, 평균회귀, 변동성·거래량, KST 세션·VWAP, 시간순 온라인 메타 필터를 포함했습니다.

| 후보 | 정상 청산 | 승률 | Wilson 하한 | 기본 수익률 | 2배 비용 수익률 | 양수 fold | 판정 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `trend_daily_sma50_above_sma200` | 1 | 100.0% | 20.65% | +4.08% | +3.77% | 1/6 | 표본·Wilson 하한 부족 |
| `trend_daily_close_above_sma200` | 5 | 60.0% | 23.07% | +9.70% | +8.06% | 2/6 | 승률·표본·fold 부족 |
| `donchian_4h_55_20_breakout` | 20 | 35.0% | 18.12% | +0.50% | -5.38% | 3/6 | 승률·비용 스트레스 실패 |
| `dc_30m_bb20_rsi14_armed_reentry_5pct_exit` | 54 | 50.0% | 37.11% | -19.20% | -31.52% | 2/6 | 수익·MDD·반복성 실패 |

승률과 기대수익은 다릅니다. 적은 이익을 자주 얻고 큰 손실을 드물게 보는 전략은 승률이 높아도 수수료와 슬리피지 후 손실일 수 있으므로, 승인 기준은 승률만으로 구성하지 않았습니다.

## 백테스트 안전성 보정

이번 연구 전에 다음 실행·집계 오류를 막는 회귀 검증을 추가했습니다.

- 30분 데이터 갭이 나오면 갭 이전 주문을 다음 관측 봉에 뒤늦게 체결하지 않고 첫 관측 시가에서 강제 `FLAT`
- `maximum_order_krw`와 KST 기준 `maximum_daily_entries`를 백테스트에도 적용
- 마지막 봉의 평가용 강제청산은 자산곡선에만 반영하고 정상 청산 거래 수·승률에서는 제외
- OOS 시작은 실제 `FLAT`에서 시작하며 훈련 중 열린 합성 포지션을 새 OOS 진입으로 만들지 않음
- 추세·세션 후보의 진입 기준가는 신호 봉 종가가 아니라 실제 다음 봉 시가
- 메타 필터는 보정 표본 부족 시 낮은 임계값으로 우회하지 않고 신규 진입을 차단

Validator는 데이터·후보 manifest·공통 실행 엔진 소스·결과 해시, 별도 지표·게이트 재계산, 결과 미러 일치, 설치된 LaunchAgent plist·런타임 wrapper·`launchctl` 환경의 신규 진입 잠금을 검사했습니다. 검증 결과는 `passed=true`이며 전략 승격은 별개로 `forbidden`입니다.

## 재현

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
PYTHONPATH=src .venv/bin/python scripts/run_winrate_research.py
PYTHONPATH=src .venv/bin/python scripts/validate_winrate_research.py
```

연구 산출물:

- `.omx/specs/autoresearch-winrate70/result.json`
- `.omx/specs/autoresearch-winrate70/validation.json`
- `reports/krw-btc-winrate70-research-2026-08-24.json`

## 외부 근거와 한계

- [Freqtrade 전략 가이드](https://www.freqtrade.io/en/stable/strategy-101/)는 공개 전략을 출발점으로만 사용하고 자체 백테스트와 드라이런을 요구합니다.
- [Freqtrade lookahead analysis](https://docs.freqtrade.io/en/stable/lookahead-analysis/)는 미래 정보 누수를 별도로 검사해야 함을 설명합니다.
- [Freqtrade backtesting assumptions](https://docs.freqtrade.io/en/stable/backtesting/)는 역사적 체결 가정이 실거래 체결을 증명하지 못함을 명시합니다.
- [Bailey et al., Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659)은 많은 후보를 시험한 뒤 최고 성과만 선택할 때 생기는 선택 편향을 다룹니다.

역사적 백테스트는 미래 수익을 보장하지 않습니다. 다음 연구 단계는 동일 개발 데이터의 추가 미세조정이 아니라 새로운 전진 데이터 축적과 페이퍼 체결 검증입니다.
