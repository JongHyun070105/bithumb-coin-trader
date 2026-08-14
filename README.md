# Bithumb Coin Trader

빗썸 KRW 현물 시장을 대상으로 전략 연구부터 페이퍼 관찰, 라이브 전환 심사, 주문 실행 안전성까지 하나의 흐름으로 검증하는 트레이딩 시스템입니다.

이 프로젝트의 목표는 백테스트 수익률만 제시하는 것이 아니라 다음 조건을 코드와 검증 가능한 상태로 연결하는 것입니다.

- 룩어헤드 없는 데이터 수집과 워크포워드 평가
- 비용·슬리피지 스트레스를 포함한 전략 승격 기준
- 실제 주문과 분리된 영속 페이퍼 원장
- 상태·감사 로그·거래소 응답 간 불일치 시 실패 폐쇄
- 모호한 주문 결과에 대한 재주문 금지와 재조정
- Discord 운영 알림과 자동 실행

> 현재 상태: **NOT_READY / RESEARCH_ONLY** — 페이퍼 관찰은 동작하지만 라이브 자동매매는 승인되지 않았습니다.

## 프로젝트 범위

현재 구현 범위는 빗썸 KRW 현물 `LONG / FLAT` 전략입니다.

- 빗썸 공개 API 기반 완료 일봉·분봉 수집과 시간대 집계
- 일봉·4시간·1시간 추세·돌파 기준선과 30분 고정 후보 전략 비교
- 비중첩 워크포워드 및 비용 스트레스 평가
- 일봉 단위 페이퍼 실행과 catch-up
- 읽기 전용 MCP 계정·주문 가능 상태 점검
- 라이브 주문 계획, 사전 위험 검증, 체결 재조정
- 주문 및 페이퍼 상태 Discord 알림

숏 포지션, 레버리지, 대여 상품, 타 거래소 연동은 현재 실행 범위에 포함하지 않습니다.

## 시스템 흐름

```text
빗썸 공개 시세
    ↓
데이터 검증·고정 해시
    ↓
워크포워드 연구·비용 스트레스
    ↓
RESEARCH_ONLY 또는 PAPER_CANDIDATE
    ↓
영속 페이퍼 실행·감사 원장 검산
    ↓
읽기 전용 MCP 실전 준비 점검
    ↓
READY 심사
```

`READY`는 주문 허가가 아니라 운영 검토를 시작할 수 있다는 의미입니다. CLI에는 라이브 주문 명령을 노출하지 않습니다.

## 현재 연구 기준선

빗썸 공개 API에서 수집한 `KRW-BTC` 일봉 1,000개로 고정 추세·돌파 전략을 평가했습니다.

| 구분 | 결과 |
|---|---:|
| 전체 구간 참고 수익률 | +37.53% |
| 전체 구간 최대 낙폭 | 9.63% |
| 워크포워드 OOS 복리 수익률 | **-4.70%** |
| OOS 수익 fold | 1 / 6 |
| OOS 거래 수 | 9 |
| 2배 비용 스트레스 | **-7.25%** |

전체 구간 결과는 인샘플 참고치입니다. 독립 테스트 구간과 비용 스트레스 조건을 통과하지 못했으므로 현재 전략은 `RESEARCH_ONLY`이며 라이브 승격이 차단됩니다.

데이터 식별 해시와 상세 결과는 [연구 기준선](docs/RESEARCH_BASELINE.md)과 [고정 보고서](reports/krw-btc-daily-baseline-2026-08-10.json)에 기록되어 있습니다.

### 다중 시간대 고정 후보 비교

RSI·볼린저 재진입, 추세 필터, 시계열 모멘텀, Donchian 돌파 등 고정한 16개 후보를 완료된 `KRW-BTC` 30분봉 40,000개와 동일한 8개 연속 OOS fold에서 비교했습니다.

| 후보 | OOS 수익률 | 최대 낙폭 | 거래 수 | 2배 비용 |
|---|---:|---:|---:|---:|
| 4시간 SMA50 필터 평균회귀 | +1.02% | 0.72% | 6 | -0.79% |
| 일봉 종가/SMA200 추세 | +0.17% | 10.18% | 3 | -0.73% |
| EMA200 필터 평균회귀 | -1.99% | 3.51% | 7 | -4.03% |
| 원문 전략 + 일봉 SMA140 필터 | -2.04% | 11.45% | 16 | -6.65% |
| 일봉 종가/SMA140 추세 | -2.38% | 11.82% | 3 | -3.26% |

수익률 1위도 거래 수·수익 fold·비용 스트레스·마지막 1,600개 미사용 봉을 통과하지 못했습니다. 적응적으로 추가한 두 번째 후보군은 별도 전진 검증 없이는 승격할 수 없습니다. 따라서 새 후보는 선택하지 않았고 기존 페이퍼 전략도 교체하지 않았습니다. 전체 16개 순위와 fold별 결과는 [고정 후보 연구](docs/CANDIDATE_RESEARCH_2026-08-12.md)에 있습니다.

### Wave 3 지표·앙상블 연구

2026-08-13 연구에서는 ADX, MACD, PVO, 거래범위 돌파와 고정 다수결 앙상블을 추가했습니다. 후보 5개, 확장형 중첩 선택 규칙, 비용과 bootstrap 계약을 해시로 고정해 재현 가능하게 만들었습니다. 다만 과거 40,000봉과 추가 48봉 모두 manifest 고정 전에 관찰 가능했으므로 전부 역사·사후 진단이며 전진 증거로 주장하지 않습니다.

| 진단 결과 | OOS 수익률 | 최대 낙폭 | 거래 수 | 2배 비용 |
|---|---:|---:|---:|---:|
| 3-of-5 일봉 앙상블 | +2.24% | 7.98% | 1 | +1.93% |
| MACD + PVO | -1.16% | 4.13% | 6 | -2.93% |
| 50일 거래범위 돌파·1% 밴드 | -4.40% | 13.65% | 2 | -4.97% |
| SMA50/200 + ADX14 | -7.98% | 7.98% | 7 | -9.89% |

앙상블의 숫자는 양수지만 청산 거래가 한 건뿐이어서 전략 근거로 사용할 수 없습니다. 과거 전체를 누적하는 확장형 outer/inner 워크포워드에서는 8개 fold 모두 통과 후보가 없어 Cash를 선택했고 수익률은 0%였습니다. 이전 연구 1위 대비 일별 초과수익 bootstrap의 95% 구간도 `-2.36% ~ +0.48%`로 0을 포함합니다. 추가 48봉은 manifest 고정 전 관찰 가능한 사후 진단이므로 승격 증거가 아닙니다.

따라서 현재 결론은 계속 `RESEARCH_ONLY`입니다. 상세 후보, fold 회계, 비용 스트레스, bootstrap과 재현 명령은 [Wave 3 연구](docs/CANDIDATE_RESEARCH_2026-08-13.md)에 있습니다.

### Wave 4 두 자아 반증 연구

2026-08-14에는 탐색자와 비평자가 후보·실패 기준을 먼저 토론한 뒤 84일 모멘텀, 저변동성 gate, train-only 볼륨-클록을 평가했습니다.

| 후보 | OOS 수익률 | MDD | non-final 거래 | 수익 fold | 2배 비용 |
|---|---:|---:|---:|---:|---:|
| 84일 모멘텀 | +5.95% | 3.16% | 1 | 2/8 | +5.62% |
| RV20 중앙값 gate | 0.00% | 0.00% | 0 | 0/8 | 0.00% |
| 볼륨-클록 | -42.55% | 42.55% | 178 | 0/8 | -50.38% |

84일 모멘텀은 숫자만 높고 수익이 단 한 거래에 100% 집중돼 기각했습니다. 볼륨-클록은 충분히 거래했지만 강한 손실로 반증됐습니다. nested 선택기는 8/8 fold에서 Cash를 골랐고, 이전 1위 대비 bootstrap 95% 하한은 `-2.32%`였습니다. 새 후보 동결 후의 전진 표본은 아직 0봉이므로 결론은 계속 `RESEARCH_ONLY`입니다. 상세한 두 자아의 합의, gap 처리, 여덟 반증 gate와 재현 절차는 [Wave 4 연구](docs/CANDIDATE_RESEARCH_2026-08-14.md)에 있습니다.

## 주요 구성요소

| 모듈 | 역할 |
|---|---|
| `data.py` | 빗썸 일봉·분봉 수집, 완료 봉 필터링, 시간대 집계, 데이터셋 해시 |
| `indicators.py` / `strategy.py` | RSI·볼린저·추세·돌파 후보와 완료 상위 시간대 신호 |
| `backtest.py` / `research.py` | 다음 봉 시가 체결, 동일 fold 후보 비교, 워크포워드, 비용 스트레스 |
| `paper.py` | Decimal 회계, WAL 복구, 감사 원장 replay |
| `readiness.py` | 연구·페이퍼·상태·MCP를 묶은 라이브 준비 심사 |
| `execution.py` | 주문 계획, 위험 제한, preflight, 단일 제출, 재조정 |
| `mcp_client.py` | 공식 빗썸 MCP의 읽기·쓰기 경계와 최소 환경 |
| `discord_notify.py` | 페이퍼·차단·접수·체결·모호 상태 알림 |

## 설치

Python 3.11 이상과 Node.js 18 이상이 필요합니다. MCP 서버는 고정된 `@bithumb-official/bithumb-mcp` 버전을 `npx`로 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

API 키는 저장소에 기록하지 않고 실행 환경에서만 주입합니다.

```text
BITHUMB_ACCESS_KEY
BITHUMB_SECRET_KEY
```

`.env`, `data/`, `state/`, 실행 중 생성되는 보고서는 Git에서 제외됩니다.

## 사용법

공개 일봉 수집:

```bash
.venv/bin/bithumb-trader fetch \
  --market KRW-BTC \
  --count 1000 \
  --output data/krw-btc-daily.csv
```

워크포워드 연구:

```bash
.venv/bin/bithumb-trader research --input data/krw-btc-daily.csv
```

완료 30분봉에서 고정 후보 16개 비교:

```bash
.venv/bin/bithumb-trader fetch-minutes \
  --market KRW-BTC \
  --unit 30 \
  --count 40000 \
  --to 2026-08-12T11:30:00+00:00 \
  --as-of 2026-08-12T11:30:00+00:00 \
  --output data/krw-btc-30m-2026-08-12-wave2.csv

PYTHONPATH=src .venv/bin/bithumb-trader research-candidates \
  --input data/krw-btc-30m-2026-08-12-wave2.csv \
  --as-of 2026-08-12T11:30:00+00:00 \
  --train-size 19200 \
  --test-size 2400 \
  --output reports/krw-btc-candidate-study-2026-08-12.json
```

Wave 3 중첩 선택·지표 연구 재현:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_wave3_research.py \
  --input data/krw-btc-30m-2026-08-13-wave3.csv \
  --output reports/krw-btc-wave3-study-2026-08-13.json \
  --generated-at 2026-08-13T12:20:00+00:00

PYTHONPATH=src .venv/bin/python scripts/validate_wave3_research.py \
  reports/krw-btc-wave3-study-2026-08-13.json
```

Wave 4 두 자아·train-only 연구 재현:

```bash
PYTHONPATH=src .venv/bin/python scripts/run_wave4_research.py \
  --input data/krw-btc-30m-2026-08-14-wave4.csv \
  --output reports/krw-btc-wave4-study-2026-08-14.json \
  --generated-at 2026-08-14T11:29:00+00:00

PYTHONPATH=src .venv/bin/python scripts/validate_wave4_research.py \
  reports/krw-btc-wave4-study-2026-08-14.json \
  --input data/krw-btc-30m-2026-08-14-wave4.csv
```

주문 없이 최신 연구 신호 확인:

```bash
.venv/bin/bithumb-trader signal --market KRW-BTC
```

완료된 일봉을 페이퍼 원장에 반영하고 상태 확인:

```bash
.venv/bin/bithumb-trader paper-run --notify
.venv/bin/bithumb-trader paper-status
```

읽기 전용 MCP를 포함한 라이브 준비 상태 확인:

```bash
.venv/bin/bithumb-trader live-readiness --probe-mcp
```

`NOT_READY`는 안전 차단을 의미하며 exit code `2`를 반환합니다. 이 명령은 API 키 값, Discord 대상 값, 계정 상세를 출력하지 않고 주문도 제출하지 않습니다.

페이퍼 자동 실행 스케줄 설치:

```bash
.venv/bin/bithumb-trader paper-schedule-install
```

스케줄러는 주기적으로 완료 봉을 확인하되 일봉 하나당 결정을 정확히 한 번만 기록합니다. 실행을 놓친 경우 저장된 전략 포지션을 기준으로 누락된 완료 봉을 순서대로 처리합니다.

## 페이퍼 원장

페이퍼 상태는 pending WAL을 거쳐 원자적으로 저장됩니다. 감사 원장에는 각 결정의 전체 상태, 체결 가정, 수수료, 손익, 평가금액과 canonical SHA-256이 기록됩니다.

상태 조회 시 원장을 처음부터 replay하여 다음 항목을 다시 계산합니다.

- 현금과 자산 수량
- 취득 원가와 수수료
- 실현·미실현 손익
- 진입·청산 순서
- 최종 상태와 감사 로그의 일치 여부

부분 기록이나 프로세스 중단이 발생하면 pending WAL로 마지막 트랜잭션을 복구합니다. 과거 레코드 손상이나 회계 불일치는 추정해서 수정하지 않고 라이브 승격을 차단합니다.

## 전략 승격 기준

- 확정 종가로 생성한 신호를 다음 봉 시가에 체결
- 현재 봉을 돌파 채널 계산에서 제외
- 수수료와 슬리피지를 모든 체결에 반영
- 학습·워밍업 구간과 독립 테스트 구간 분리
- 미래 데이터, 동일 봉 종가 체결, 누락값 전방 채움 금지
- 충분한 OOS 거래 수
- 과반수 수익 fold
- 비용 적용 후 양의 OOS 수익률
- 강화된 비용 스트레스에서도 양의 수익률

모든 연구 조건을 통과해야 `PAPER_CANDIDATE`가 됩니다. 이후에도 정해진 기간의 페이퍼 기록, 충분한 완결 거래, 회계 불일치 0건, 읽기 전용 MCP 점검을 모두 만족해야 `READY` 심사가 가능합니다.

## 실행 안전장치

MCP 클라이언트의 기본 명령은 `--read-only`입니다. 라이브 쓰기 경로는 다음 조건을 모두 요구합니다.

1. `TradingSettings.mode == LIVE`
2. `BITHUMB_LIVE_TRADING=true`
3. 런타임 확인 토큰 `CONFIRM_BITHUMB_LIVE_ORDER`

그 뒤에도 다음 검증을 통과해야 합니다.

- 마켓 활성 상태와 지원 주문 방향·유형
- 거래소 최소 주문금액과 사용 가능 잔고
- 설정한 수수료 가정 이하의 실제 수수료
- 구성된 주문 한도와 위험 한도
- 로컬 상태와 주문 계획의 일치
- 활성 또는 결과 불명확 주문 부재

주문 직전 `client_order_id`와 주문 의도를 원자적으로 저장합니다. 결과가 불명확한 POST는 자동 재시도하지 않고 `untracked` 상태로 차단하며, 동일 ID 조회로 체결 수량을 재조정한 후에만 다음 행동을 허용합니다.

## Discord 알림

Discord 대상은 저장소 밖의 로컬 설정 또는 환경변수에서 읽습니다.

```bash
.venv/bin/bithumb-trader discord-setup
.venv/bin/bithumb-trader discord-test
```

알림은 다음 상태를 구분합니다.

- 페이퍼 실행 — 항상 `실주문 없음` 표시
- 위험·잔고·최소금액 검증에 의한 주문 차단
- 거래소 주문 접수 — 체결로 표시하지 않음
- 결과 불명확 — 자동 재주문 금지 표시
- 주문 조회 결과 대기·체결·취소

알림 실패는 주문 결과를 변경하거나 같은 주문을 다시 보내는 조건으로 사용되지 않습니다.

## 테스트

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check
```

## 문서

- [연구 기준선](docs/RESEARCH_BASELINE.md)
- [다중 시간대 고정 후보 연구](docs/CANDIDATE_RESEARCH_2026-08-12.md)
- [Wave 3 지표·앙상블 연구](docs/CANDIDATE_RESEARCH_2026-08-13.md)
- [Wave 4 두 자아 반증 연구](docs/CANDIDATE_RESEARCH_2026-08-14.md)
- [실전 준비 런북](docs/LIVE_READINESS.md)

## 공식 문서

- [빗썸 AI Trade Kit MCP 안내](https://apidocs.bithumb.com/docs/mcp)
- [빗썸 주문 요청 API](https://apidocs.bithumb.com/reference/%EC%A3%BC%EB%AC%B8-%EC%9A%94%EC%B2%AD)
- [빗썸 주문가능조회 안내](https://apidocs.bithumb.com/docs/api-%ED%98%B8%EC%B6%9C%ED%95%B4-%EB%B3%B4%EA%B8%B0)
- [빗썸 일봉 조회 API](https://apidocs.bithumb.com/reference/%EC%9D%BCday-%EC%BA%94%EB%93%A4-%EC%A1%B0%ED%9A%8C)
- [빗썸 분봉 조회 API](https://apidocs.bithumb.com/reference/%EB%B6%84minute-%EC%BA%94%EB%93%A4-%EC%A1%B0%ED%9A%8C)

## 면책

이 프로젝트는 트레이딩 시스템 연구 및 소프트웨어 검증을 위한 프로젝트입니다. 전략 결과는 미래 수익을 보장하지 않으며, 라이브 거래에는 원금 손실 가능성이 있습니다.
