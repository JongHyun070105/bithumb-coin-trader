# Strategy V9 72시간 수집 최종 인프라 감사

- 분류: **72H PROCESS SOAK PASS / DATA QUALITY FAIL / RESEARCH ONLY**
- collector PID: `30933`
- uninterrupted start: `2026-08-26 01:19:33 KST`
- 72시간 경계: `2026-08-29 01:19:33 KST`
- SIGINT 관찰: `2026-08-29 01:27:33 KST`
- 프로세스 종료 관찰: `2026-08-29 01:38:50 KST`
- machine-readable ledger: `reports/v9_72h_soak_final_audit_2026-08-29.json`
- provenance: `docs/V9_EPOCH_PROVENANCE_PID_30933_2026-08-26.md`

## 결론

PID 30933은 동일 프로세스로 72시간을 초과해 실행됐으므로 **continuous process soak 자체는 PASS**다. 그러나 이 epoch는 데이터 품질과 인과적 재현성 결함이 남아 있어 **alpha research ready가 아니며 live trading ready도 아니다**.

잘못 생성됐던 초기 자동 보고서의 `duplicate_trade_ids=0`, `queue_dropped_events=0`, `lossless_storage_verified=true`, `data_quality_verified=true`, `alpha_research_allowed=true` 주장은 근거가 없어 폐기했다. 최종 ledger는 검증 불가능한 운영 지표를 0으로 치환하지 않는다.

## 종료 및 artifact freeze

01:27 KST bounded pre-shutdown 확인에서 PID 30933 하나만 존재했고 raw append가 ACTIVE였으며 uptime은 72시간을 초과했다. 정확한 PID에 SIGINT를 한 번 전달한 뒤 raw append가 중단되고 old V9 finalizer가 manifest 5,396개를 생성하는 것을 관찰했다. 프로세스는 01:38:50 KST에 종료됐다.

collector가 종료된 뒤 current V9.1 manifest generator로 `--rehash-all --include-current-hour`를 실행했다.

- raw partitions: **5,396**
- manifest files: **5,396**
- total raw bytes: **79,398,011,919 bytes (73.95 GiB)**
- full rehash generated/repaired: **5,396**
- generation failures: **0**
- missing/orphan/invalid/path-size mismatch: **0 / 0 / 0 / 0**
- zero-byte raw files: **0**

각 schema-v4 manifest는 해당 raw partition 전체를 binary SHA-256으로 읽으면서 record/schema/timestamp/duplicate 통계를 생성했다. 따라서 아래 수치는 5,396개 final partition 전체를 대상으로 한 **FULL-SCAN** 결과다.

## FULL-SCAN 결과

| 지표 | 결과 | 판정 |
|---|---:|---|
| records | 70,936,202 | MEASURED FULL-SCAN |
| invalid UTF-8 / JSON | 0 | PASS |
| schema mismatch | 0 | PASS |
| missing required fields | 0 | PASS |
| non-finite numeric | 0 | PASS |
| malformed stored timestamps | 0 | PASS |
| local receive timestamp reversals | **277** | **FAIL** |
| Binance orderbook `UNKNOWN` market | **9,417,381** | **FAIL** |
| partition-local duplicate trade IDs | **198** | **FAIL** |
| exchange/local offset outliers | **118,186** | FINDING |
| missing monotonic receive timestamp | **70,936,202** | **FAIL / V9 LIMITATION** |

거래소·stream별 주요 결함:

- Binance/orderbook: 9,417,381 records 전부 `market=UNKNOWN`, exchange timestamp도 없음.
- Bithumb/trade: partition-local duplicate IDs 192건.
- Upbit/trade: partition-local duplicate IDs 6건.
- local wall-clock reversal: Binance orderbook 17, Binance trade 35, Bithumb orderbook 144, Bithumb ticker 12, Bithumb trade 12, Upbit orderbook 50, Upbit trade 7건.

마지막 balanced sample에서 Bithumb ticker/trade p95가 약 10~11초로 관찰됐다. 이 값은 exchange-labelled timestamp와 local receive timestamp의 차이이며 clock offset, timestamp 의미, publication, network, queue가 합쳐진 값이다. one-way network latency로 해석하지 않는다.

## 검증 불가능한 항목

V9 epoch에는 durable operational metrics가 없으므로 다음 항목은 0이 아니라 **NOT VERIFIABLE**이다.

- queue dropped events
- reconnect count와 정확한 disconnect 원인
- writer error count
- collector exit code(분리 실행된 프로세스라 wait status 없음)
- exchange feed completeness
- replay determinism

raw SNAPSHOT burst는 반복 reconnect의 간접 증거이므로 `reconnect_free_collection=FAIL`을 유지한다. quarantine file 0개는 upstream loss가 없었다는 증명이 아니다.

## Epoch provenance 경계

최종 감사는 다음 세 대상을 분리한다.

1. PID 30933의 실제 process identity와 관찰된 V9 raw layout.
2. process 시작 이후 만들어진 reference commit `6085218` — launch-time source proof가 아님.
3. 현재 V9.1 working tree — PID 30933이 로드하지 않은 개선 코드.

정확한 launch-time in-memory source fingerprint는 **NOT DIRECTLY VERIFIABLE**이다. 현재 working tree나 현재 main HEAD를 V9 72시간 수집 코드라고 쓰지 않는다.

## 최종 게이트

| 게이트 | 상태 |
|---|---|
| continuous 72h process soak | PASS |
| final manifest coverage and path/size integrity | PASS |
| local raw parse/schema integrity | PASS |
| Binance orderbook identity | FAIL |
| strict causal receive order | FAIL |
| duplicate-free local persistence | FAIL |
| queue drops | NOT VERIFIABLE |
| reconnect-free collection | FAIL |
| exchange feed completeness | NOT DIRECTLY VERIFIABLE |
| replay determinism | NOT VERIFIABLE |
| alpha research ready | **false** |
| live trading ready | **false** |

V9 raw epoch는 결함 발견과 infrastructure soak 증거로 보존하되 cross-market alpha dataset으로 승격하지 않는다. Binance symbol 식별, monotonic receive clock, durable metrics, fail-closed writer와 graceful drain 개선은 **V9.1 새 epoch에서만** 검증한다.
