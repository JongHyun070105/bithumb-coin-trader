# 실전 준비 런북

이 문서는 라이브 주문을 켜는 방법이 아니라, 라이브 검토를 시작하기 전에 반드시 충족해야 하는 증거와 장애 대응 절차를 정의합니다. 현재 상태는 `NOT_READY`입니다.

## 자동 점검 기준

`bithumb-trader live-readiness --probe-mcp`는 다음 항목이 모두 참일 때만 `READY`를 표시합니다.

1. 연구 보고서가 `PAPER_CANDIDATE`
2. 페이퍼 관찰 30일 이상
3. 페이퍼 결정 100건 이상
4. 완결된 진입·청산 30회 이상
5. 상태와 JSONL 감사 원장의 회계 불일치 0건
6. 활성 또는 결과 불명확 주문 0건
7. Discord Finance Chat 대상 설정 정상
8. API 키 환경변수 이름 2개 존재
9. 점검 중 `TRADING_MODE=paper`, `BITHUMB_LIVE_TRADING=false`
10. 읽기 전용 MCP 주문가능조회에서 마켓, 수수료, 최소금액, 통화, 잔고 검증 통과

`READY`는 주문 허가가 아닙니다. 사람의 최종 검토 전에는 라이브 플래그와 CLI 주문 표면을 계속 잠가 둡니다.

## 일상 운영

```bash
.venv/bin/bithumb-trader paper-status
.venv/bin/bithumb-trader live-readiness --probe-mcp
tail -n 100 state/paper-cron.log
```

페이퍼 상태는 pending WAL을 거쳐 원자적으로 저장되고, 각 결정은 `state/paper.jsonl`에 전체 상태 스냅샷과 canonical SHA-256으로 기록됩니다. 시작 시 WAL을 복구하고, 상태 조회는 원장을 처음부터 재생해 현금·수량·원가·수수료·손익·최종 상태를 검산합니다. 동일 일봉 재실행은 `already_processed`가 되며 가상 거래나 감사 이벤트를 중복 생성하지 않습니다. 프로세스 잠금으로 겹친 실행도 차단합니다.

## 장애 대응

- `paper state is stale`: 상태나 원장을 수동 수정하지 않습니다. 공개 캔들 연속성과 마지막 결정 시각을 먼저 비교하고 누락 원인을 조사합니다.
- `accounting_mismatches > 0`: 라이브 검토를 중단합니다. 상태의 `decision_count`와 JSONL의 유효한 연속 레코드 수를 대조합니다.
- MCP 인증 실패: 주문을 시도하지 않습니다. API 키 만료일, 호출 IP 허용 목록, 환경변수 주입 범위를 읽기 전용으로 확인합니다.
- `active_client_order_id` 또는 `untracked_order`: 신규 주문을 금지합니다. 같은 client order ID 조회로 실제 상태를 재조정하기 전에는 상태를 지우지 않습니다.
- Discord 실패: 거래 결과를 재시도하거나 추정하지 않습니다. 알림만 복구하고 거래소 상태는 별도로 조회합니다.
- 전략이 `RESEARCH_ONLY`: 페이퍼 시스템은 운영 안정성 자료만 수집합니다. 해당 결과를 라이브 수익성 증거로 승격하지 않습니다.

## 즉시 중단 장치

라이브 플래그는 기본적으로 꺼져 있습니다. 향후 라이브 검토 중이라도 다음 값을 유지하거나 되돌리면 주문 경로가 닫힙니다.

```text
TRADING_MODE=paper
BITHUMB_LIVE_TRADING=false
```

CLI에는 라이브 주문 명령이 없습니다. 모호한 주문 결과를 진단하려고 같은 주문을 다시 보내지 않습니다.
