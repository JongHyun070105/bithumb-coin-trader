# V9.1 안정화 검증 기록 — 2026-08-29

- 분류: **LOCAL DEPLOYMENT-READINESS VALIDATION / RESEARCH ONLY**
- 기존 V9 epoch: 종료·freeze 완료, 수정하지 않음
- 실행 위치: `/tmp/v91-short.qjoSsd` 격리 경로
- 외부 범위: Bithumb, Binance, Upbit 공개 websocket만 사용
- AWS resource: 생성하지 않음
- trading/account backend: 연결하지 않음

## 검증 결과

수정된 V9.1 collector를 기존 V9 raw 경로와 분리해 120초간 실행하고 정상적인 duration 종료와 final manifest 생성을 확인했다.

| 항목 | 결과 | 판정 |
|---|---:|---|
| 실행 시간 | 120초 | PASS |
| raw records | 13,211 | MEASURED |
| stream partitions / manifests | 7 / 7 | PASS |
| manifest schema | v4 only | PASS |
| Binance orderbook market | `BTCUSDT` | PASS |
| `UNKNOWN` market | 0 | PASS |
| collector run ID 누락 | 0 | PASS |
| monotonic receive timestamp 누락 / invalid / reversal | 0 / 0 / 0 | PASS |
| queue drop / backpressure | 0 / 0 | MEASURED FOR THIS RUN |
| writer error / unpersisted event | 0 / 0 | MEASURED FOR THIS RUN |
| reconnect / disconnect | 0 / 0 | MEASURED FOR THIS RUN |

수집된 stream은 Binance orderbook/trade, Bithumb orderbook/ticker/trade, Upbit orderbook/trade의 7종이다. durable metrics snapshot은 schema version 1, collector run ID, 시작 시각, 거래소별 연결·처리·queue·writer counter를 포함했다.

## 소프트웨어 검증

- Python unit tests: **478 PASS**
- adversarial infrastructure gates: **15/15 PASS**
- Python compileall: **PASS**
- final V9 ledger fail-closed assertions: **PASS**
- dashboard typecheck/lint/tests/build: **PASS**
- dashboard dependency audit: **0 vulnerabilities**
- git diff check / diff secret scan: **PASS**

## 해석 제한과 다음 게이트

이 검증은 코드의 startup, public websocket subscription, market attribution, timestamp/run ID, durable metrics, writer drain, manifest 생성을 확인하는 짧은 deployment-readiness 검사다. 120초 무결점은 장기 안정성, exchange feed completeness, replay determinism, 연구 적합성 또는 실거래 적합성을 증명하지 않는다.

따라서 `alpha_research_ready=false`, `live_trading_ready=false`를 유지한다. AWS provisioning 전에는 계정의 실제 credit eligibility와 서울 리전 최신 가격을 다시 확인하고, 비용과 생성 대상에 대한 별도 승인을 받아야 한다. AWS 배포가 승인되면 Mac epoch와 섞지 않는 새 environment/epoch로 시작한다.
