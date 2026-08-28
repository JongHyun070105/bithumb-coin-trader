# 현재 프로젝트 상태 — 2026-08-29

## 기준점

- repository/branch: `JongHyun070105/bithumb-coin-trader` / `main`
- 상태 검토 시작 HEAD: `ace187b58c1aace4a7025447e8c595e7d0c0ed75`
- V9.1 implementation freeze commit: `947d435ddd124f584ca11a5462991463403a0527`
- 이 문서를 포함하는 최종 handoff commit은 `git log -1 -- docs/CURRENT_PROJECT_STATUS_2026-08-29.md`로 식별한다. 문서가 자기 자신의 commit SHA를 주장하지 않도록 한다.
- 기준 시점 working tree: clean, `origin/main`과 동일

## 공식 상태

| 영역 | 상태 | 의미 |
|---|---|---|
| V4/V6 | **FROZEN BASELINE** | 역사적 연구 baseline. 자동 승격·실거래 근거가 아님 |
| V8/V8.1 | **REJECTED** | V9 연구 규약에서 공식 폐기. 재승격하지 않음 |
| V9 | **CLOSED — SOAK COMPLETE / DATA QUALITY FAIL** | 인프라 soak·실패 분석·감사 회귀 증거로만 보존 |
| V9.1 | **FROZEN LOCAL BASELINE** | local deployment-readiness short validation 통과 |
| Dashboard | **READ-ONLY UI FOUNDATION** | 한국어 mock/development UI v0.1, backend 미연결 |
| AWS | **NEXT PHASE / NOT PROVISIONED** | 비용·credit·설계·승인 전 resource 없음 |
| Alpha research | **BLOCKED / NOT READY** | V9 raw를 alpha dataset으로 사용하지 않음 |
| Live trading | **BLOCKED / DISABLED** | 주문·계좌·live control 비활성 |

## V9 최종 공식 판정

- 72H PROCESS SOAK: **PASS**
- DATA QUALITY: **FAIL**
- ALPHA RESEARCH READY: **FALSE**
- LIVE TRADING READY: **FALSE**

V9 raw epoch는 infrastructure soak evidence, failure analysis, audit regression, V9.1 개선 근거로만 보존한다. alpha development, cross-market signal validation, live evidence, causal microstructure 연구에는 사용하지 않는다.

Machine-readable ledger와 최종 보고서가 고정한 FULL-SCAN 결과:

| 지표 | 값 |
|---|---:|
| raw partitions / manifests | 5,396 / 5,396 |
| raw bytes | 79,398,011,919 bytes (73.95 GiB) |
| records | 70,936,202 |
| Binance orderbook `UNKNOWN` | 9,417,381 |
| local receive wall-clock reversal | 277 |
| partition-local duplicate trade IDs | 198 |
| exchange/local offset outliers | 118,186 |
| missing monotonic timestamp | 70,936,202 |

V9 queue/drop/reconnect/writer counter는 **NOT VERIFIABLE**, exchange feed completeness는 **NOT DIRECTLY VERIFIABLE**, replay determinism은 **NOT VERIFIABLE**이다.

## Provenance 경계

서로 혼합하지 않는 세 대상:

1. PID 30933의 검증된 process identity와 observed raw epoch.
2. process 시작 뒤 생성된 `608521870a31e2579ca310eb90e53c86c861da50` reference snapshot.
3. `947d435ddd124f584ca11a5462991463403a0527`의 V9.1 implementation.

`6085218`은 launch-time exact source가 아니다. 정확한 launch-time in-memory source fingerprint는 **NOT DIRECTLY VERIFIABLE**이다.

## V9.1 baseline matrix

`TESTED`는 unit/adversarial test 또는 120초 isolated public-websocket validation으로 확인됐다는 뜻이다. 짧은 실행의 0 counter는 장기 안정성 증명이 아니다.

| 항목 | 분류 | 근거와 한계 |
|---|---|---|
| Binance orderbook market/symbol preservation | **TESTED** | combined-stream parser unit test, short validation의 `BTCUSDT` |
| Binance orderbook `UNKNOWN` 제거 | **TESTED** | short validation 13,211 records에서 0건 |
| local wall receive timestamp | **TESTED** | raw schema persistence와 manifest timestamp 검사 |
| local monotonic receive timestamp | **TESTED** | unit test 및 short validation missing/invalid/reversal 0 |
| collector run ID | **TESTED** | raw와 durable metrics에 동일 run ID 기록 |
| durable metrics | **TESTED** | atomic snapshot unit test와 schema-v1 short-run artifact |
| queue/drop/backpressure accounting | **TESTED** | bounded queue adversarial unit test와 short-run counters |
| reconnect/disconnect accounting | **IMPLEMENTED** | 각 websocket loop와 durable metrics에 counter 존재; 실제 reconnect recovery 장기 검증은 미완료 |
| writer failure accounting | **TESTED** | disk-write failure adversarial tests가 unpersisted count 검증 |
| writer fail-closed | **TESTED** | fatal writer error 전파·producer cancel·queue accounting 검증 |
| graceful queue drain | **TESTED** | duration shutdown short validation과 writer cleanup tests |
| raw partition append | **IMPLEMENTED** | record 단위 append-only JSONL; crash atomicity/fsync durability는 검증하지 않음 |
| atomic metrics/manifest replacement | **TESTED** | temp file 후 replace, tmp 잔존 없음 검증 |
| manifest schema v4 | **TESTED** | stale schema 거부, short validation 7/7 v4 |
| measured/estimated/not-verifiable status 구분 | **TESTED** | bounded status tests와 종료 V9 status 출력 |
| disk projection correction | **TESTED** | 이미 수집한 bytes를 이중 차감하지 않는 unit test |
| balanced exchange×stream sampling | **TESTED** | stream별 sampling과 stale group unit tests |

분류상 `IMPLEMENTED`인 reconnect recovery와 raw append crash durability는 AWS 장기 soak에서 재검증해야 하며, V9.1을 장기 안정성 PASS로 표현하지 않는다.

## V9.1 local validation

공식 의미: **LOCAL DEPLOYMENT-READINESS SHORT VALIDATION PASS**.

- duration 120s
- 13,211 records
- 7 streams / 7 manifests
- `UNKNOWN` 0
- monotonic missing/invalid/reversal 0/0/0
- queue drops 0
- writer errors 0
- reconnect/disconnect 0/0

이는 장기 안정성, feed completeness, replay determinism, alpha 또는 live readiness를 증명하지 않는다.

## Dashboard

- UI v0.1, 한국어, dark/compact, responsive
- Overview, Collector Health, Safety Center, Logs/Events 구현
- Trading, Performance, Research Lab, AWS/Infrastructure는 placeholder
- mock/development fixture만 사용
- production control, live toggle, account/order/API/backend 연결 없음
- desktop/mobile browser smoke와 event filter 동작 확인

Dashboard는 실제 collector/trading source of truth가 아니다.

## 환경·비밀정보 경계

- `.env`, `.env.local`: Git ignore 대상
- 개인자산 allowlist 및 `TRUMP`: 두 파일에서 marker 부재 확인
- API key 값: 검사·문서·출력에 포함하지 않음
- AWS credential: tracked diff에 없음
- raw data, runtime state, private artifact: baseline commit 대상 아님
- tracked `reports/v9_72h_soak_final_audit_2026-08-29.json`은 의도적으로 공개 가능한 fail-closed 감사 ledger이며 raw/private report가 아님

## 완료된 자동화

- `v9-72h-preflight-only`: 실행이 끝난 `COUNT=1` rule, **PAUSED**
- `v9-72h-final-freeze-audit`: 실행이 끝난 `COUNT=1` rule, **PAUSED**

두 automation은 V9.1/AWS 작업에 재사용하지 않으며 새 recurrence를 만들지 않는다. Codex automation backend와 persisted TOML 양쪽에서 `PAUSED`를 확인한다.

## 알려진 제한과 다음 단계

- V9은 실패 증거로 닫혔으며 연구 dataset이 아니다.
- V9.1 short validation은 120초뿐이다.
- reconnect recovery, host crash durability, deterministic replay, compression/archive 복구는 아직 end-to-end 검증되지 않았다.
- AWS credit와 가격은 시간에 따라 변하므로 provisioning 직전에 실제 계정과 공식 서울 리전 가격을 다시 확인한다.
- 다음 단계는 AWS 계획·비용 검토와 provisioning 승인이다. AWS resource, 장기 collector, alpha mining, holdout, paper/live trading은 이 baseline freeze에서 시작하지 않는다.
