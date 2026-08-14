# KRW-BTC 두 자아 Wave 4 연구 — 2026-08-14

## 결론

이번 연구의 결론도 `RESEARCH_ONLY`입니다. 후보 선택, 페이퍼 전략 교체, 라이브 변경은 없습니다.

탐색자 Persona A가 문헌 기반 후보를 제안하고, 비평자 Persona B가 과적합·거래 수·비용·수익 집중도 기준으로 반박했습니다. 두 자아가 세 차례 직접 교환한 뒤 후보와 실패 기준을 먼저 동결하고 결과를 계산했습니다.

| 후보 | 기본 OOS | MDD | non-final 거래 | 수익 fold | 2배 비용 | 판정 |
|---|---:|---:|---:|---:|---:|---|
| 84일 시계열 모멘텀 | +5.9455% | 3.1610% | 1 | 2/8 | +5.6241% | 거래 1건·수익 집중 100%로 기각 |
| 84일 모멘텀 + RV20 gate | 0.0000% | 0.0000% | 0 | 0/8 | 0.0000% | 무거래로 기각 |
| train-only 볼륨-클록 | -42.5451% | 42.5451% | 178 | 0/8 | -50.3794% | 강하게 반증 |

고정 후보의 가장 높은 숫자만 보면 84일 모멘텀이 좋아 보입니다. 그러나 하나의 청산이 전체 양의 PnL 100%를 만들었고 8개 중 2개 fold만 양수입니다. 이를 수익 전략으로 인정하지 않았습니다. 반대로 볼륨-클록은 거래 178건으로 표본 수는 충분했지만 손실이 커서 가설 자체가 Bithumb KRW-BTC에 전이되지 않는다는 강한 반증이 됐습니다.

inner 검증을 통과한 후보가 없어서 expanding nested 선택기는 8/8 outer fold 모두 Cash를 선택했습니다. 중첩 정책 수익률은 기본·2배 비용 모두 0%이며, 이전 연구 1위 대비 7일 moving-block bootstrap 95% 하한은 `-2.3200%`입니다. 여덟 개 승격 gate 중 MDD와 형식적 집중도 gate만 통과했습니다.

## 데이터와 증거 경계

| 구분 | 값 |
|---|---|
| 시장 | `KRW-BTC` 현물 LONG / FLAT |
| 전체 고정 구간 | 40,095개 30분봉, 2024-04-28 13:30 UTC ~ 2026-08-14 10:30 UTC |
| 전체 SHA-256 | `4a40c01ffd7974ad3893bdd34fcbe7b48a894ef21503bfd4ed901b689909f1b2` |
| 반복 관찰 역사 | 첫 40,000봉, 2026-08-12 11:00 UTC까지 |
| 역사 SHA-256 | `dc3537c862bc54efebfd215807e2ab57da66396ebfbfcf3d5a243327b9817248` |
| Wave 4 manifest | `1b36e392930c8bc29682442ac7a9e200c741f4cc13264d7b087ac59afe99874b` |
| Wave 4 신규 forward | 0봉 — 데이터 cutoff가 후보 동결보다 18분 앞섬 |

과거 40,000봉은 Wave 1~3에서 이미 반복 관찰한 적응적 역사입니다. 뒤의 95봉도 Wave 4 후보 동결 전에 관찰 가능했으므로 Wave 4 전진 증거가 아닙니다. Wave 3에서 동결한 nested 정책의 이후 47봉 행동은 Cash, 수익률 0%였지만 하루 미만 표본이라 충분하지 않습니다.

전체 구간에는 gap event 16개, 누락 봉 124개, 최대 간격 630분이 있습니다. 누락을 채우지 않고 timestamp gap에서 즉시 FLAT으로 초기화하며, 다음 완전한 KST 일봉 신호가 생기기 전에는 이전 LONG을 이어받지 않습니다.

## 두 자아가 동결한 후보

### Persona A — 탐색자

1. `daily_tsmom_84`
   - 완료 KST 일봉 종가가 84일 전 종가보다 높으면 LONG
   - 84일은 문헌의 1~12개월 사전 범위 안에서 정한 약 3개월 horizon
2. `daily_tsmom_84_rv20_median_gate`
   - 1번 조건에 현재 RV20이 직전 252개 RV20 중앙값 이하라는 binary gate 추가
   - 부분 비중·레버리지는 사용하지 않음
3. `intraday_volume_clock_first_last_momentum`
   - outer/inner train의 완전한 KST 일자 거래량만으로 평균 거래량이 가장 큰 30분 slot `s`를 fit
   - `s` 봉이 양수면 정확히 47 slot 뒤 시가에 진입해 한 봉 보유하고 다음 시가에 청산
   - test volume은 anchor를 바꿀 수 없고 `s→s+47` 사이 gap이 있으면 cycle을 skip

### Persona B — 비평자

- 10% target-vol 부분비중안은 최소주문·고정 allocation과 충돌하므로 삭제
- funding·미결제약정·basis가 없는 공개 OHLCV로 carry proxy를 만들지 않음
- 84일 두 후보는 같은 momentum family이므로 독립 확인 하나로만 계산
- non-final 거래 12건 미만 또는 단일 거래가 양의 PnL 50%를 넘으면 기각
- 동일 과거를 다시 본 결과는 수치와 무관하게 승격 금지

문헌은 가설 생성 근거일 뿐 Bithumb 수익성 증명이 아닙니다.

- [Moskowitz, Ooi & Pedersen — Time Series Momentum](https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf)
- [Moreira & Muir — Volatility-Managed Portfolios](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12513)
- [Shen, Urquhart & Wang — Bitcoin intraday time series momentum](https://onlinelibrary.wiley.com/doi/10.1111/fire.12290)
- [Szetela et al. — The relationship between trend and volume on the bitcoin market](https://link.springer.com/article/10.1007/s40822-021-00166-5)
- [Bithumb Candlestick REST API](https://apidocs.bithumb.com/v1.2.0/reference/candlestick-rest-api)

## 반증 gate

중첩 정책은 다음을 모두 만족해야 하지만 실제로는 2/8개만 통과했습니다.

- 기본 수익률이 이전 동일 구간 1위 `+1.019286%`보다 큼: 실패
- 2배 비용 수익률 > 0: 실패
- MDD ≤ 10%: 통과
- 양수 outer fold ≥ 5/8: 실패
- 양수 stress quarter ≥ 3/4: 실패
- 이전 1위 대비 7일 bootstrap 95% 하한 > 0: 실패
- non-final 청산 ≥ 12: 실패
- 단일 거래 양의 PnL 기여 ≤ 50%: 거래가 없어 형식상 통과, 표본 gate에서 차단

따라서 `overall_pass=false`, `selected_candidate=null`, `can_promote=false`입니다.

## 재현

```bash
.venv/bin/bithumb-trader fetch-minutes \
  --market KRW-BTC --unit 30 --count 40200 \
  --as-of 2026-08-14T11:00:00+00:00 \
  --output data/krw-btc-30m-2026-08-14-wave4.csv

PYTHONPATH=src .venv/bin/python scripts/run_wave4_research.py \
  --input data/krw-btc-30m-2026-08-14-wave4.csv \
  --output reports/krw-btc-wave4-study-2026-08-14.json \
  --generated-at 2026-08-14T11:29:00+00:00

PYTHONPATH=src .venv/bin/python scripts/validate_wave4_research.py \
  reports/krw-btc-wave4-study-2026-08-14.json \
  --input data/krw-btc-30m-2026-08-14-wave4.csv \
  --result .omx/specs/autoresearch-btc-wave4/result.json
```

Validator는 raw CSV에서 전체 보고서를 결정론적으로 다시 실행하고 manifest, train-only fit, 비용, 연속 OOS 곡선, fold 회계, 거래 표본, bootstrap, 여덟 gate와 비승격 상태를 검사합니다. 현재 결과는 `passed`, `issues=[]`, `replay_performed=true`입니다. 기계 판독 결과는 [Wave 4 보고서](../reports/krw-btc-wave4-study-2026-08-14.json)에 있습니다.
