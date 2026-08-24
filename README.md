# Bithumb Coin Trader

빗썸 KRW 현물 시장용 **연구·검증·안전 실행 프레임워크**입니다. 공개 OHLCV 데이터의 시간순 백테스트와 실제 거래소 체결 원장을 분리하며, 검증을 통과하지 못한 전략은 자동으로 실전에 승격하지 않습니다.

현재 운영 경계는 `LONG / FLAT`입니다. 빗썸 현물 API에는 공매도 실행을 연결하지 않았으며 피라미딩은 비활성화되어 있습니다. 원장으로 추적되는 신규 포지션은 매도액과 잔여액이 각각 최소 주문금액 이상일 때만 `+2%`에서 50% 분할익절하고, 4시간 뒤 손익이 `±0.6%` 안에 머물면 타임컷합니다. 배포 전에 존재하던 포지션에는 이 강화 청산 규칙을 소급 적용하지 않습니다.

## 핵심 구조

- `strategy.py`, `wave5.py`: 재현 가능한 현물 전략과 후보 비교
- `backtest.py`, `research.py`: 닫힌 봉 신호, 다음 봉 시가 체결, 시간순 워크포워드 및 비용 스트레스
- `execution.py`: 주문 가능 정보 사전 조회, 고유 `client_order_id`, 단일 주문 제출, 조회 기반 체결 확정
- `fill_ledger.py`: 거래소가 반환한 개별 체결 ID·가격·수량·수수료만 기록하는 append-only 원장
- `risk.py`: 신규 위험 노출 제한과 보호성 청산의 분리
- `discord_notify.py`: 주문·차단·체결·청산 알림. 자식 프로세스에는 최소 환경만 전달
- `ai_brain.py`, `gemini_council.py`: 엄격한 스키마 검증을 거친 연구 보조. 명시적 허용 전에는 실전 설정을 바꾸지 않음

실시간 포지션 감시는 약 3초 목표 주기로 메인 스레드에서 실행합니다. 최대 25개 시장 스캔은 단일 읽기 전용 백그라운드 작업으로 분리하며, 완료 결과를 사용할 때 포지션·미확정 주문·쿨다운·조정 상태를 다시 검사합니다.

## 빗썸 API 활용

실행 경로는 다음 순서를 따릅니다.

1. 경보·공지·시세·호가 데이터 확인. 필수 조회 실패 시 신규 진입 차단
2. `account_get_order_chance`로 수수료, 최소 주문금액, 가용 잔고 재검증
3. 불변 `client_order_id`로 주문을 한 번만 제출
4. `trade_get_order`를 반복 조회해 `done` 또는 `cancel` 확인
5. 실제 `trades` 체결 목록을 원장에 기록하고 계좌 잔고와 재조정

연구용 MCP 설정은 `.mcp.json`에서 공식 패키지 버전을 고정하고 `market,account --read-only`로 제한합니다. 주문 실행은 별도의 최소 권한 경로를 사용합니다.

## 설치

요구사항은 Python 3.11 이상과 Node.js 18 이상입니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

API 키는 저장소 파일이나 채팅에 입력하지 마십시오. 출금 권한이 없는 거래 전용 키를 사용하고, 빗썸에서 허용 IP를 제한한 뒤 로컬 비밀 저장소 또는 권한이 `0600`인 환경 파일로만 주입하십시오.

기본 상태에서는 `BITHUMB_NEW_ENTRIES`가 없으므로 신규 매수가 차단됩니다. 보호성 청산 감시는 계속할 수 있지만, 신규 진입 스위치는 키 회전·실계좌 상태 확인·forward 검증을 모두 마친 운영자가 직접 관리해야 합니다.

현재 저장소는 macOS LaunchAgent를 자동 설치하지 않습니다. 데몬은 `scripts/run_daemon_macos.sh`로 수동 실행하며, 재부팅 후 자동 복구가 필요하면 운영 환경의 권한과 비밀 저장소를 별도로 검증해야 합니다.

## 검증

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 scripts/run_wave5_research.py
PYTHONPATH=src python3 scripts/validate_wave5_research.py \
  .omx/specs/autoresearch-wave5/result.json \
  --data data/krw-btc-30m-2026-08-14-wave4.csv
```

Wave 5 결과는 `.omx/specs/autoresearch-wave5/`에 생성됩니다. 결과의 `can_promote`는 항상 `false`이며, 후보가 검증 게이트를 통과하지 못하면 현금 대기를 선택합니다.

## 안전 원칙

- 주문 POST가 타임아웃 나면 재제출하지 않고 같은 `client_order_id`를 조회합니다.
- 미확정 주문, 손상된 상태 파일, 경보·호가 조회 실패는 신규 진입을 차단합니다.
- 손실 한도와 일일 진입 한도는 보호성 전량 매도를 막지 않습니다.
- 실제 체결 손익은 OHLC 종가가 아니라 거래소 체결 원장에서 계산합니다.
- 부분매도는 요청 수량을 별도 상태로 보존하며, 상태·원장·거래소 잔고가 모두 일치한 뒤에만 완료 처리합니다.
- Wave 5와 AI 연구 설정은 별도 검증을 통과하지 않으면 라이브 전략으로 승격하지 않습니다.
- 백테스트 성과는 미래 수익을 보장하지 않으며, 연구 결과는 자동으로 실전 설정을 변경하지 않습니다.
