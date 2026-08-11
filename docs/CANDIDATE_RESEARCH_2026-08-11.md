# KRW-BTC 고정 후보 전략 비교 — 2026-08-11

## 결론

이번에 비교한 다섯 전략 중 페이퍼 후보로 승격할 수 있는 전략은 없다. 원문을 재현한 30분봉 RSI·볼린저 전략은 기본 비용 OOS에서 **-21.09%**, 비용 2배 스트레스에서 **-30.87%**였다. 가장 나았던 4시간 추세 필터 평균회귀는 OOS **+1.02%**였지만 거래가 6건뿐이고 비용 스트레스가 **-0.79%**, 마지막 미사용 구간 거래가 0건이어서 우위로 볼 수 없다.

따라서 현재 일봉 페이퍼 전략은 교체하지 않으며 프로젝트 상태는 `RESEARCH_ONLY`를 유지한다.

## 사전 고정한 가설

파라미터는 OOS 결과를 보기 전에 고정했다. 이후 임계값을 바꿔 재탐색하지 않았다.

1. 원문 재현 현물형: 30분 BB(20, 2), Wilder RSI(14), RSI 35 이하에서 하단 밴드 이탈 후 복귀 매수. 직전 완료 KST 일봉이 `(BB 하단 + SMA20) / 2` 아래면 RSI 기준을 20으로 강화. 종가에서 5% 익절·손절을 관찰하고 다음 30분봉 시가에 청산.
2. 1시간 평균회귀: 전 봉이 BB 하단 밖, 현재 봉이 밴드 안으로 복귀하고 RSI가 30을 상향 돌파하면 매수. BB 중단선 또는 24시간 후 청산.
3. 2번 + 1시간 EMA200 상승 필터.
4. 2번 + 마지막으로 완료된 4시간봉 종가가 4시간 SMA50 위인 경우만 진입.
5. 1시간 스퀴즈 돌파: BB 폭이 최근 120봉 하위 20%이면서 상단 밴드를 상향 돌파할 때 매수, 중단선 이탈 청산.

빗썸 현물 실행 범위에 맞춰 모두 `LONG / FLAT`이며 숏·레버리지는 포함하지 않았다.

## 데이터와 검증 계약

- 시장: 빗썸 `KRW-BTC`
- 원천 데이터: 완료된 30분봉 30,000개
- 기간: 2024-11-22 07:00 UTC ~ 2026-08-11 08:30 UTC
- 정규화 OHLCV 데이터셋 manifest SHA-256: `28ebcf074fba31bca87462b846853ebae49a715a35944941025a1836d534f463`
- 워크포워드: 과거 8,640개 30분봉, 미래 2,880개 테스트, 총 7개 비중첩 OOS fold
- 신호·체결: 확정 종가 신호, 다음 30분봉 시가 체결
- 기본 비용: 편도 수수료 25bp + 슬리피지 5bp
- 스트레스: 편도 수수료 50bp + 슬리피지 10bp
- 1시간 전략: 완료된 두 개의 30분봉으로만 1시간봉을 만들고, 두 번째 30분봉이 끝난 뒤에만 신호를 노출
- 마지막 미사용 구간: 2026-07-17 09:00 UTC ~ 2026-08-11 08:30 UTC

12개 공백 구간에서 빠진 30분봉은 총 100개이며 가장 긴 간격은 630분이었다. 공백은 임의로 채우지 않았고, 1시간·4시간·일봉 집계에서 구성 봉이 빠진 버킷은 폐기했다.

7개 OOS fold는 하나의 연속 실행으로 평가했다. 폴드 경계에서 보유 포지션을 강제청산하지 않으며, 마지막 OOS 끝에서만 평가 종료 청산을 구분해 기록한다.

## 결과

| 전략 | OOS 수익률 | 최대낙폭 | 거래 | 수익 fold | 2배 비용 |
|---|---:|---:|---:|---:|---:|
| 4시간 SMA50 필터 평균회귀 | **+1.02%** | 0.72% | 6 | 2 / 7 | -0.79% |
| EMA200 필터 평균회귀 | -1.99% | 3.51% | 7 | 2 / 7 | -4.03% |
| 스퀴즈 돌파 | -17.87% | 18.02% | 63 | 1 / 7 | -32.05% |
| 무필터 평균회귀 | -19.07% | 21.39% | 51 | 1 / 7 | -30.58% |
| 원문 30분 전략 | **-21.09%** | **29.46%** | 44 | 2 / 7 | **-30.87%** |
| 동일 비용 Buy & Hold 참고 | -20.23% | 27.14% | 1 | - | - |

4시간 필터형은 수익률 순위만 보면 1위지만, 7개 fold 중 5개가 무거래이고 전체 거래도 6건뿐이다. 마지막 미사용 1,200개 30분봉에서도 거래가 0건이었다. 표본 부족과 비용 민감성을 동시에 실패했으므로 선택하지 않는다.

## 원문과 공개 구현에서 채택한 부분

- 사용자 제공 원문: RSI·볼린저 이탈 후 재진입 상태 머신, 일봉 레짐에 따른 RSI 강화, 5% 청산 가설.
- [DCInside 자동매매 검증 글](https://gall.dcinside.com/mgallery/board/view/?id=ag&no=106): 미래 봉 금지, 현실 비용, 모의 운용, 파라미터 완만성, 장애·중복 주문 안전장치.
- [Bollinger Bands 공식 규칙](https://www.bollingerbands.com/bollinger-band-rules): 밴드 터치 자체는 신호가 아니며 밴드 밖 종가는 추세 지속일 수도 있다는 반대가설.
- [fastquant RSI 구현](https://github.com/enzoampil/fastquant/blob/805c4440bf96ba04cfd43aaf4926e4b45f3c3f33/python/fastquant/strategies/rsi.py#L18-L58)과 [Bollinger 구현](https://github.com/enzoampil/fastquant/blob/805c4440bf96ba04cfd43aaf4926e4b45f3c3f33/python/fastquant/strategies/bollinger_band.py#L18-L58): 단순 기준선.
- [meta-strategy 워크포워드 구조](https://github.com/trsdn/meta-strategy/blob/99705b5acff3d44613d7223b7cda1e903d1afd17/README.md#L21-L41): 추세 필터와 시간 순서 검증.
- [crypto-trading-bot 스퀴즈 구현](https://github.com/jicheolha/crypto-trading-bot/blob/9851eadd96c44db99a26d4ca3cbabcc53b199242/technical.py#L137-L195): 평균회귀의 반대인 변동성 압축 후 돌파 가설.
- [Freqtrade lookahead 분석](https://docs.freqtrade.io/en/stable/lookahead-analysis/): 전체 데이터프레임 계산에서 생길 수 있는 미래정보 누출 점검 원칙.

외부 저장소의 수익률 주장은 가져오지 않았고, MIT 라이선스 구현에서 규칙의 형태만 참고해 이 저장소의 데이터·비용·체결기로 다시 계산했다.

## 주식 연구와의 비교

`toss-auto-trader-lab`의 최신 주식 후보도 포트폴리오 수준 검증 손익은 양수였지만 종목별 비용 후 edge가 평균 -0.582%이고 유효 종목 edge가 0개라 라이브 승격이 차단되어 있다. 주식 결과는 KRX 일봉, 다른 보유기간·비용·배분을 사용하므로 BTC 수익률과 숫자를 직접 비교할 수 없다.

현재 결론은 “코인이 주식보다 낫다”가 아니라 **양쪽 모두 아직 검증된 실전 edge가 없다**이다. 다음 비교는 자산군별 동일한 연환산 수익, 최대낙폭, 거래 수, 비용 스트레스, 미사용 홀드아웃을 갖춘 뒤 결정해야 한다.

## 재현

```bash
.venv/bin/bithumb-trader fetch-minutes \
  --market KRW-BTC \
  --unit 30 \
  --count 30000 \
  --to 2026-08-11T09:00:00+00:00 \
  --as-of 2026-08-11T09:00:00+00:00 \
  --output data/krw-btc-30m-2026-08-11.csv

.venv/bin/bithumb-trader research-candidates \
  --input data/krw-btc-30m-2026-08-11.csv \
  --as-of 2026-08-11T09:00:00+00:00 \
  --train-size 8640 \
  --test-size 2880 \
  --output reports/krw-btc-candidate-study-2026-08-11.json

python3 scripts/validate_candidate_research.py \
  reports/krw-btc-candidate-study-2026-08-11.json
```

기계 판독 결과는 [`reports/krw-btc-candidate-study-2026-08-11.json`](../reports/krw-btc-candidate-study-2026-08-11.json)에 고정했다.
