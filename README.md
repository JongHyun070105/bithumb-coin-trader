# Bithumb Coin Trader

2만원 소액 계좌를 위한 **연구 우선·실패 폐쇄(fail-closed)** 암호화폐 트레이딩 프레임워크입니다. 현재 단계는 자동 수익 봇이 아니라 데이터 수집, 룩어헤드 없는 백테스트, 워크포워드 검증, 페이퍼/라이브 실행 경계를 검증하는 기반입니다.

> 현재 상태: **NOT_READY / RESEARCH_ONLY** — 페이퍼 관찰 중, 라이브 자동매매 승인 안 됨

## 현재 결론

- 빗썸 Open API/MCP는 현물 매수·매도를 지원합니다.
- 빗썸 렌딩 상품은 별도로 존재하지만 최소 10만원이며, 공식 Open API와 MCP에는 대여·숏 실행 도구가 없습니다.
- 이 저장소의 현재 범위는 빗썸 KRW 현물 `LONG / FLAT`뿐입니다. 숏과 타 거래소 연동은 포함하지 않습니다.
- 계정에서 확인된 수수료는 매수·매도 각각 0.25%, 최소 주문은 5,000원입니다. 실제 주문 직전에는 MCP `account_get_order_chance`로 다시 확인합니다.
- 기본 자본금은 20,000원, 단일 포지션 상한은 10,000원, 현금 완충은 5,000원입니다.

## 검증된 기준선

2026-08-10에 빗썸 공개 API에서 `KRW-BTC` 일봉 1,000개(2023-11-14~2026-08-09)를 조회해 고정 추세·돌파 전략을 평가했습니다.

| 구분 | 결과 |
|---|---:|
| 전체 구간 참고 수익률 | +37.53% |
| 전체 구간 최대 낙폭 | 9.63% |
| 워크포워드 OOS 복리 수익률 | **-4.70%** |
| OOS 수익 fold | 1 / 6 |
| OOS 거래 수 | 9 |
| 2배 비용 스트레스 | **-7.25%** |

전체 구간 결과는 인샘플 참고치일 뿐입니다. 독립 테스트 구간에서 실패했기 때문에 수익성은 확인되지 않았고 라이브 승격은 차단됩니다. 데이터 식별 해시와 결과 JSON은 [연구 기준선](docs/RESEARCH_BASELINE.md)과 [고정 보고서](reports/krw-btc-daily-baseline-2026-08-10.json)에 기록했습니다.

## 다음 연구 순서

1. 빗썸 KRW 현물 중 거래대금과 호가 유동성이 충분한 시장만 사전 필터링
2. 일봉 기준선을 유지한 채 4시간봉 추세·돌파 후보군을 학습 fold 안에서만 선택
3. 실제 계정 수수료, 2배 비용 스트레스, 최소 주문금액 미달 청산 위험을 함께 평가
4. 승격 기준을 통과한 경우에만 30일 페이퍼 관찰을 시작

“최대한의 수익”은 사후 최고 수익률이 아니라, 보지 않은 구간에서도 비용 후 양수인 전략을 찾는 것으로 정의합니다. 현재 기준선은 그 조건을 통과하지 못했습니다.

## 설치

Python 3.11 이상과 Node.js 18 이상이 필요합니다. MCP는 `@bithumb-official/bithumb-mcp`를 `npx`로 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

API 키는 저장소에 넣지 않습니다. 이미 설치한 Codex MCP와 동일하게 OS 환경변수만 사용합니다.

```text
BITHUMB_ACCESS_KEY
BITHUMB_SECRET_KEY
```

`.env`, `data/`, `state/`, 실행 보고서는 Git에서 제외됩니다.

## Discord Finance Chat 알림

기존 Toss 자동매매가 사용하는 `hermes send → finance-chat` 대상을 사용자 전용 설정 파일 `~/.config/bithumb-coin-trader/env`로 복사하고 연결을 확인할 수 있습니다. 채널 ID는 출력하거나 Git에 저장하지 않습니다.

```bash
.venv/bin/bithumb-trader discord-setup
.venv/bin/bithumb-trader discord-test
```

현재 이 기기에는 동일 Finance Chat 대상이 `BITHUMB_DISCORD_TARGET`으로 설정되어 있으며 테스트 메시지 전송까지 확인했습니다. 설정 파일 권한은 `0600`입니다. Hermes에는 거래소 API 키를 제외한 최소 환경만 전달하고, 저장소가 아닌 임시 폴더에서 실행합니다.

페이퍼 실행 결과도 `페이퍼 일일 실행 (실주문 없음)`으로 명확히 구분해 보냅니다. 실주문 접수 알림과 혼동하지 않도록 모든 페이퍼 메시지에 `실주문 없음`이 포함됩니다.

실행기는 다음 이벤트를 서로 다른 문구로 알립니다.

- 위험·잔고·최소금액 검증에 의한 주문 차단
- 거래소 주문 접수 — 체결로 표시하지 않음
- 타임아웃 등 결과 불명확 — 재주문 금지와 추적 차단 표시
- 주문 상세 조회 결과 대기·체결·취소

알림 전송 실패는 이미 접수된 주문을 실패처럼 보이게 만들지 않습니다. 따라서 알림 장애 때문에 주문을 다시 보내는 경로가 없습니다. 라이브 설정으로 `BithumbExecutor`를 기본 생성하면 로컬 Finance Chat 설정을 자동으로 사용하며, 테스트에서는 알림기를 명시적으로 주입할 수 있습니다.

## 사용법

공개 일봉을 명시한 파일에 저장:

```bash
.venv/bin/bithumb-trader fetch --market KRW-BTC --count 1000 --output data/krw-btc-daily.csv
```

빗썸 현물 기준 워크포워드 연구:

```bash
.venv/bin/bithumb-trader research --input data/krw-btc-daily.csv
```

실행하지 않고 최신 신호만 확인:

```bash
.venv/bin/bithumb-trader signal --market KRW-BTC
```

완료된 일봉 한 개를 페이퍼 원장에 반영하고 상태 확인:

```bash
.venv/bin/bithumb-trader paper-run --notify
.venv/bin/bithumb-trader paper-status
```

실전 준비 상태를 읽기 전용 MCP 계정 점검까지 포함해 확인:

```bash
.venv/bin/bithumb-trader live-readiness --probe-mcp
```

`NOT_READY`는 정상적인 안전 차단 결과이므로 exit code `2`를 반환합니다. 이 명령은 API 키 값, Discord 채널 ID, 잔고 상세를 출력하지 않으며 주문을 제출하지 않습니다.

이 Mac에는 다음 명령으로 페이퍼 전용 스케줄을 설치할 수 있습니다.

```bash
.venv/bin/bithumb-trader paper-schedule-install
```

설치된 cron은 매시 10분에 상태를 확인하지만, 완료된 일봉 하나당 결정은 정확히 한 번만 기록합니다. Mac이 잠자기 상태여서 정시 실행을 놓친 경우 다음 실행 때 누락된 완료 일봉을 순서대로 따라잡습니다. 로그와 원장은 `state/`에만 저장되고 Git에는 포함되지 않습니다.

## 전략 및 검증 계약

- EMA 20/80 추세 + 이전 20봉 고가/저가 돌파
- 현재 봉을 채널 계산에서 제외
- 종가 확정 후 생성한 신호를 다음 봉 시가에 체결
- 편도 수수료 0.25%, 기본 슬리피지 5bp
- 스트레스 테스트는 편도 수수료 0.50%, 슬리피지 10bp
- 400봉 학습/워밍업 + 100봉 독립 테스트, 비중첩 6개 fold
- 미래 데이터, 누락값 전방 채움, 동일 봉 종가 체결 금지
- 30개 OOS 거래, 과반 수익 fold, 비용 후 양수, 2배 비용 양수를 모두 만족해야 `PAPER_CANDIDATE`

## 실행 안전장치

MCP 클라이언트의 기본 명령은 `--read-only`입니다. 쓰기 명령은 별도 `LIVE_COMMAND`를 명시해야 하며, 코드상 다음 세 조건을 모두 요구합니다.

1. `TradingSettings.mode == LIVE`
2. `BITHUMB_LIVE_TRADING=true`
3. 런타임 확인 토큰 `CONFIRM_BITHUMB_LIVE_ORDER`

그 뒤에도 주문가능조회 preflight, 엄격한 주문 payload 검증, 고유 `client_order_id`, 단 한 번의 주문 호출을 적용합니다. 주문 직전 `client_order_id`와 의도를 원자적으로 저장하고, 모호한 POST는 자동 재시도하지 않은 채 `untracked`로 차단합니다. 다음 주문은 동일 ID 조회로 체결 수량과 포지션을 재조정한 뒤에만 가능합니다. CLI에는 라이브 주문 명령을 노출하지 않았습니다.

라이브 검토 기준과 장애 대응 절차는 [실전 준비 런북](docs/LIVE_READINESS.md)에 정리했습니다. 현재 기준선은 `PAPER_CANDIDATE`가 아니므로 페이퍼 기록이 충분해져도 전략 연구가 개선되기 전에는 `READY`가 되지 않습니다.

## 테스트

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
git diff --check
```

## 공식 근거

- [빗썸 AI Trade Kit MCP 안내](https://apidocs.bithumb.com/docs/mcp)
- [빗썸 주문 요청 API](https://apidocs.bithumb.com/reference/%EC%A3%BC%EB%AC%B8-%EC%9A%94%EC%B2%AD)
- [빗썸 주문가능조회 안내](https://apidocs.bithumb.com/docs/api-%ED%98%B8%EC%B6%9C%ED%95%B4-%EB%B3%B4%EA%B8%B0)
- [빗썸 일봉 조회 API](https://apidocs.bithumb.com/reference/%EC%9D%BCday-%EC%BA%94%EB%93%A4-%EC%A1%B0%ED%9A%8C)
- [빗썸 거래 정책](https://www.bithumb.com/customer_support/info_guide?seq=536)
- [빗썸 렌딩 최소금액 FAQ](https://support.bithumb.com/hc/ko/articles/52813989066137-%EB%8C%80%EC%97%AC-%EA%B0%80%EB%8A%A5-%EA%B8%88%EC%95%A1%EC%9D%80-%EC%96%BC%EB%A7%88%EC%9D%B8%EA%B0%80%EC%9A%94)

## 면책

이 프로젝트는 연구용 소프트웨어입니다. 백테스트와 신호는 수익을 보장하지 않으며, 라이브 거래에는 원금 손실 가능성이 있습니다.
