# KRW-BTC Wave 3 지표·앙상블 연구 — 2026-08-13

## 결론

이번 연구는 `RESEARCH_ONLY`입니다. 새 후보를 페이퍼 또는 라이브 전략으로 선택하지 않았습니다.

- 고정 후보 5개 중 `ensemble_daily_3_of_5`가 진단 OOS `+2.2403%`, 2배 비용 `+1.9318%`로 가장 높았습니다.
- 그러나 전체 OOS에서 청산 거래는 1건, 수익 fold는 2/8뿐입니다. 표본이 너무 작아 수익 근거가 아닙니다.
- 각 outer 학습 구간 내부의 여섯 inner fold만 사용한 중첩 선택기는 8/8 fold에서 통과 후보가 없어 Cash를 선택했습니다.
- 중첩 정책 OOS는 수익률 0%, 거래 0건입니다. 이전 연구 1위는 같은 구간에서 `+1.0193%`, 2배 비용 `-0.7896%`였습니다.
- 중첩 정책과 이전 1위의 KST 일별 초과 로그수익을 7일 블록으로 5,000회 재표본한 95% 구간은 `-2.3561% ~ +0.4756%`입니다. 하한이 0보다 크지 않습니다.
- 추가 48개 30분봉은 manifest 고정 전에 이미 관찰 가능했으므로 `posthoc_shadow`로만 보존했습니다. 전진 증거나 승격 근거가 아닙니다.

수익률을 높이려는 시도 자체보다, 결과를 본 뒤 규칙을 바꾸지 않고 약한 결과도 남기는 것이 이번 연구의 핵심입니다. 수익을 보장하거나 실거래를 승인하는 결과가 아닙니다.

## 데이터 경계

| 구분 | 값 |
|---|---|
| 시장 | `KRW-BTC` 현물 LONG / FLAT |
| 실행 단위 | 완료 30분봉 종가 신호 → 다음 30분봉 시가 체결 |
| 전체 데이터 | 40,048봉, 2024-04-28 13:30 UTC ~ 2026-08-13 11:00 UTC |
| 전체 SHA-256 | `b8f7217eb30c9b2b55e5b0462e40d826c8c83a057e2e548fd928951156e03e07` |
| 재사용 과거 | 첫 40,000봉, 2026-08-12 11:00 UTC까지 |
| 과거 SHA-256 | `dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248` |
| 사후 shadow | 이후 48봉, 2026-08-12 11:30 UTC ~ 2026-08-13 11:00 UTC |

과거 40,000봉은 이전 연구에서 이미 관찰했습니다. 다시 분할했다고 해서 미사용 검증 구간이라고 부르지 않습니다. 이번 역사 결과는 적응적 연구 진단이며, 실제 승격에는 앞으로 쌓이는 고정 전진 증거가 필요합니다.

누락 봉은 전방 채움하지 않습니다. 전체 구간에는 16개 gap event와 124개 누락 봉이 있으며 최대 간격은 630분입니다. 완료되지 않았거나 불연속인 상위 시간대 버킷은 집계에서 제외합니다.

## 고정 후보 계약

다음 5개 후보와 실행·선택·비용·bootstrap 규칙을 코드와 canonical manifest로 고정했습니다. SHA-256은 `41afcddf791ced95f6e92751e45d8f71dacd94083d1ea5c516001407d179674a`입니다. 이 manifest는 재현성과 구현 드리프트 차단용이며, 외부 타임스탬프가 있는 사전등록 증거는 아닙니다.

1. `trading_range_daily_50_band_1pct`
   - 완료 일봉 종가가 현재 봉을 제외한 이전 50일 최고가의 101%를 넘으면 LONG
   - 이전 50일 최저가의 99% 아래면 FLAT
2. `trading_range_daily_50_no_band`
   - 같은 50일 거래범위 규칙에서 1% 밴드를 제거한 인접 변형
3. `trend_daily_sma50_200_adx14_25`
   - SMA50 > SMA200, +DI14 > -DI14, ADX14 > 25일 때만 LONG
4. `trend_daily_macd12_26_9_pvo12_26`
   - MACD > signal, MACD > 0, PVO12/26 > 0일 때만 LONG
5. `ensemble_daily_3_of_5`
   - 365일 시계열 모멘텀, 50일 거래범위, SMA50/200, ADX 추세, MACD/PVO 중 3개 이상 LONG일 때 LONG

ADX는 방향이 아니라 추세 강도이고 ATR은 변동성 지표이므로, 독립적인 방향 신호처럼 사용하지 않았습니다. 지표 계산은 외부 GPL 코드를 복사하지 않고 프로젝트 안에서 독립 구현했습니다.

연구 근거:

- [Moskowitz, Ooi & Pedersen — Time Series Momentum](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf)
- [Brock, Lakonishok & LeBaron — Simple Technical Trading Rules](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x)
- [TA-Lib ADX 정의](https://ta-lib.org/functions/adx.html)
- [TA-Lib MACD 정의](https://ta-lib.org/functions/macd.html)
- [TA-Lib PVO 정의](https://ta-lib.org/functions/pvo)
- [암호화폐 모멘텀 반대 근거 — Grobys et al. 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)

학술 결과는 다른 자산·시장 구조에서 나온 가설 근거일 뿐, KRW-BTC 수익성을 입증하지 않습니다.

## 검증 설계

### 고정 후보 진단

- outer 초기 train: 19,200개 30분봉, 이후 fold마다 과거 전체를 2,400봉씩 누적
- outer test: 2,400개 30분봉
- 8개 비중첩 test fold
- test fold 전체를 하나의 OOS 계좌로 연속 실행
- fold 경계에서 가짜 청산하지 않고 실제 목표 신호가 바뀔 때만 다음 시가에 비용 반영

### 중첩 선택 정책

각 expanding outer train 안에서 끝 7,200봉을 여섯 검증 구간으로 두고, 그 이전 과거 전체를 누적 학습합니다. 첫 outer fold는 다음과 같고 이후 fold에서는 첫 학습 끝점도 2,400봉씩 늘어납니다.

```text
[0:12000] -> [12000:13200]
[0:13200] -> [13200:14400]
[0:14400] -> [14400:15600]
[0:15600] -> [15600:16800]
[0:16800] -> [16800:18000]
[0:18000] -> [18000:19200]
```

후보 통과 조건은 다음 세 가지를 모두 만족하는 것입니다.

- inner 기본비용 복리수익률 > 0
- inner 2배 비용 복리수익률 > 0
- 2배 비용 수익 fold가 6개 중 4개 이상

통과 후보는 2배 비용 수익률 내림차순, 2배 비용 MDD 오름차순, 이름순으로 고릅니다. 아무도 통과하지 못하면 Cash입니다. outer test 가격은 해당 fold의 선택 함수에 전달하지 않습니다.

## 비용

| 비용 | 기본 | 스트레스 |
|---|---:|---:|
| 체결당 수수료 | 0.25% | 0.50% |
| 체결당 슬리피지 | 5 bps | 10 bps |

비용은 진입과 청산 모두에 적용합니다. 이 가정은 실제 체결을 보장하지 않으며, 강화 비용은 내구성 검사용입니다.

## 고정 후보 결과

| 후보 | 기본 OOS | MDD | 거래 | 수익 fold | 2배 비용 |
|---|---:|---:|---:|---:|---:|
| 3-of-5 일봉 앙상블 | +2.2403% | 7.9772% | 1 | 2/8 | +1.9318% |
| MACD + PVO | -1.1604% | 4.1302% | 6 | 2/8 | -2.9291% |
| 50일 거래범위·1% 밴드 | -4.3990% | 13.6451% | 2 | 3/8 | -4.9706% |
| SMA50/200 + ADX14 | -7.9780% | 7.9780% | 7 | 0/8 | -9.8932% |
| 50일 거래범위·무밴드 | -9.0280% | 17.8265% | 3 | 3/8 | -9.8415% |

동일 구간 대조군:

| 대조군 | 기본 OOS | 2배 비용 |
|---|---:|---:|
| 이전 연구 1위: 4h SMA50 필터 평균회귀 | +1.0193% | -0.7896% |
| Buy & Hold | -17.9778% | -18.2154% |

앙상블의 결과가 가장 높아도 한 번의 포지션 결과에 크게 의존하므로 통계적 근거가 아닙니다. 두 trading-range 인접 변형은 모두 비용 스트레스에서 음수여서 파라미터 주변 안정성도 없습니다.

## 중첩 정책 및 사후 shadow

모든 outer fold에서 inner 통과 후보가 없었습니다.

| 항목 | 결과 |
|---|---:|
| Cash 선택 | 8 / 8 fold |
| 기본 OOS 수익률 | 0.0000% |
| 2배 비용 OOS 수익률 | 0.0000% |
| 거래 수 | 0 |
| 이전 1위 대비 bootstrap 점추정 | -1.0090% |
| bootstrap 95% 구간 | -2.3561% ~ +0.4756% |
| 초과수익 > 0 확률 | 15.62% |

전체 과거 40,000봉으로 마지막 정책 진단을 계산했을 때도 선택은 Cash였습니다. 이어지는 48봉에서 Cash 정책은 0%였지만, 이 구간은 manifest 고정 전에 관찰 가능했던 사후 진단입니다. 하루 동안 위험을 피했다는 관찰일 뿐 장기 우위나 전진 검증이 아니며, 어떤 후보도 이 48봉으로 승격하거나 선택하지 않았습니다.

## 신뢰성 판정

`credible_historical_improvement=false`입니다.

- 중첩 기본수익이 동일 구간 이전 1위보다 높음: 실패
- 중첩 2배 비용 양수: 실패
- 8개 중 최소 5개 outer fold 수익: 실패
- MDD 10% 이하: 통과
- 충분한 거래 또는 독립 포지션·노출 표본: 실패
- 4개 스트레스 subperiod 중 3개 양수: 실패
- 이전 1위 대비 bootstrap 95% 하한 > 0: 실패
- 인접 trading-range 변형 2개가 스트레스에서 양수: 실패

따라서 후보 선택, 페이퍼 전략 교체, 라이브 전략 변경은 모두 하지 않았습니다.

## 재현

원본 CSV는 Git에 넣지 않습니다. 공개 시세를 동일 cutoff로 다시 수집하고 해시가 다르면 연구를 중단합니다.

```bash
.venv/bin/bithumb-trader fetch-minutes \
  --market KRW-BTC --unit 30 --count 40000 \
  --to 2026-08-12T11:30:00+00:00 \
  --as-of 2026-08-12T11:30:00+00:00 \
  --output data/wave3-history.csv

.venv/bin/bithumb-trader fetch-minutes \
  --market KRW-BTC --unit 30 --count 48 \
  --to 2026-08-13T11:30:00+00:00 \
  --as-of 2026-08-13T11:30:00+00:00 \
  --output data/wave3-shadow.csv

{ head -n 1 data/wave3-history.csv; \
  tail -n +2 data/wave3-history.csv; \
  tail -n +2 data/wave3-shadow.csv; } \
  > data/krw-btc-30m-2026-08-13-wave3.csv

PYTHONPATH=src .venv/bin/python scripts/run_wave3_research.py \
  --input data/krw-btc-30m-2026-08-13-wave3.csv \
  --output reports/krw-btc-wave3-study-2026-08-13.json \
  --generated-at 2026-08-13T12:20:00+00:00

PYTHONPATH=src .venv/bin/python scripts/validate_wave3_research.py \
  reports/krw-btc-wave3-study-2026-08-13.json \
  --input data/krw-btc-30m-2026-08-13-wave3.csv
```

Validator는 raw CSV에서 보고서 전체를 다시 실행하고, 데이터·후보 manifest, 비용, 연속 equity curve와 fold 회계, nested 선택, bootstrap 설정, 사후 shadow와 비승격 상태를 fail-closed로 검사합니다. 상세 수치는 [기계 판독 보고서](../reports/krw-btc-wave3-study-2026-08-13.json)에 있습니다.
