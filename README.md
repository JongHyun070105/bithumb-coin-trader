# Bithumb Coin Trader

빗썸 KRW 현물 시장용 **연구·검증·안전 실행 프레임워크**입니다. 공개 OHLCV 데이터의 시간순 백테스트와 실제 거래소 체결 원장을 분리하며, 검증을 통과하지 못한 전략은 자동으로 실전에 승격하지 않습니다.

현재 운영 경계는 `LONG / FLAT`입니다. 빗썸 현물 API에는 공매도 실행을 연결하지 않았으며 피라미딩은 비활성화되어 있습니다. `+2%` 50% 분할익절과 4시간 `±0.6%` 타임컷은 원장·최소주문·크래시 복구까지 구현되어 있지만, 비용 반영 역사 검증에서 탈락해 활성 진입 정책에서는 제외했습니다. 2026-08-24의 37개 후보 연구에서도 개발 게이트를 모두 통과한 전략이 없어 현금 대기를 선택했으며, 설치된 데몬은 감시·알림만 수행하고 신규 매수는 잠금 상태입니다.

## 핵심 구조

- `strategy.py`, `wave5.py`: 재현 가능한 현물 전략과 후보 비교
- `backtest.py`, `research.py`: 닫힌 봉 신호, 다음 봉 시가 체결, 주문 최소금액·현금 예비금·수수료 원장·청산가 평가를 포함한 시간순 워크포워드
- `strategy_v2_research.py`, `daily_strategy_candidates.py`: 결측 없는 공식 일봉의 주간 저회전 추세 후보, 1/2/3배 비용 스트레스, prefix 미래정보 감사, White Reality Check·PBO. 과거 시행 원장이 없는 DSR은 계산 불가로 표시
- `winrate_research.py`, `winrate_*_candidates.py`: 추세·평균회귀·변동성·세션·온라인 메타 후보의 공통 사전등록 게이트
- `opportunity_research.py`, `opportunity_candidates.py`: 승률 70%를 보편 조건으로 쓰지 않는 저회전 수익 우선 연구와 단계형 게이트
- `execution.py`: 주문 가능 정보 사전 조회, 고유 `client_order_id`, 단일 주문 제출, 조회 기반 체결 확정
- `fill_ledger.py`: 거래소가 반환한 개별 체결 ID·가격·수량·수수료만 기록하는 append-only 원장
- `scan_ledger.py`: 매 스캔의 피드 건강 상태, 후보 순위, 통과·탈락 근거를 SHA-256 체인 JSONL로 기록
- `bithumb_websocket.py`: 공식 Public v1·Private v2 WebSocket 관측. 주문 권한 없이 REST/MCP 재조정 힌트만 생성
- `reference_signals.py`: 공식 공지의 발행·수정·최초 관측시각, 공식 분류, 생명주기, 관련 마켓을 보존하는 참고 전용 신호
- `market_warning_signals.py`: 공식 시장 경보의 유형·단계·종료 예정시각을 상태 변경 시에만 보존
- `external_news.py`: 선택적 Finnhub 암호화폐 헤드라인을 키 비노출 방식으로 수집하는 참고 전용 어댑터
- `weekly_research.py`: 공개 데이터 수집, 연구 실행, 독립 validator를 묶는 주간 연구 전용 작업
- `risk.py`: 신규 위험 노출 제한과 보호성 청산의 분리
- `discord_notify.py`: 주문·차단·체결·청산 알림. 자식 프로세스에는 최소 환경만 전달
- `ai_brain.py`, `gemini_council.py`: 엄격한 스키마 검증을 거친 연구 보조. 명시적 허용 전에는 실전 설정을 바꾸지 않음
- `cross_market_collector.py`, `microstructure_storage.py`: 빗썸/바이낸스/업비트 3개 거래소 실시간 체결·호가·시세 무손실 Append-Only 수집기 및 SHA-256 파티션 매니페스트 생성기 (Strategy V9 데이터 인프라)

실시간 포지션 감시는 약 3초 목표 주기로 메인 스레드에서 실행합니다. 최대 25개 시장 스캔은 단일 읽기 전용 백그라운드 작업으로 분리하며, 완료 결과를 사용할 때 포지션·미확정 주문·쿨다운·조정 상태를 다시 검사합니다. 후보가 없는 스캔과 피드 오류도 `state/scan_audit.jsonl`에 한 건씩 남습니다.

현재 실계좌 수수료 사전조회 값은 주문 때마다 다시 검증하며, 기본 운용은 하루 신규 진입 4회로 제한합니다. 외부 입출금이 flat 상태에서 감지되면 이를 수익으로 계산하지 않고 일일 손실·최대 낙폭·목표금액 기준선을 새 잔고로 재설정합니다.

## 빗썸 API 활용

실행 경로는 다음 순서를 따릅니다.

1. 경보·공지·시세·호가 데이터 확인. 필수 조회 실패 시 신규 진입 차단
2. `account_get_order_chance`로 수수료, 최소 주문금액, 가용 잔고 재검증
3. 불변 `client_order_id`로 주문을 한 번만 제출
4. `trade_get_order`를 반복 조회해 `done` 또는 `cancel` 확인
5. 실제 `trades` 체결 목록을 원장에 기록하고 계좌 잔고와 재조정

Public v1 WebSocket의 ticker/orderbook과 Private v2의 myOrder/myAsset은 관측 지연을 줄이는 보조 계층입니다. WebSocket 이벤트는 체결 확정이나 주문 근거가 아니며, Private 종료 이벤트도 기존 REST/MCP 조회를 요청할 뿐 상태·체결원장을 직접 바꾸지 않습니다. 연결이 끊기거나 데이터가 오래되면 기존 REST/MCP 경로로 대체합니다.

공식 공지는 `state/reference_events.jsonl`, 시장 경보 상태는 `state/market_warning_snapshots.jsonl`에 보존하고 Discord에 참고 정보로 표시합니다. 수정 공지는 `modified_at` 기준 새 revision으로 남기며, 경보는 상태가 달라질 때만 새 스냅샷을 추가합니다. 모든 참고 신호에는 항상 `executable=false`가 적용되며 전략 점수, 위험 승인, 주문 생성에 사용하지 않습니다.

선택적으로 `.env.local`에 `BITHUMB_FINNHUB_API_KEY`를 설정하면 10분 주기로 Finnhub의 암호화폐 헤드라인을 `state/external_news_references.jsonl`에 저장합니다. API 키는 요청 URL·로그·Discord에 포함하지 않으며 Discord에는 제목·출처·원문 링크만 표시합니다. 키가 없거나 뉴스 API가 실패해도 주문·청산·위험 판단에는 아무 영향이 없습니다.

연구용 MCP 설정은 `.mcp.json`에서 공식 패키지 버전을 고정하고 `market,account --read-only`로 제한합니다. 주문 실행은 별도의 최소 권한 경로를 사용합니다.

## 설치

요구사항은 Python 3.11 이상과 Node.js 18 이상입니다.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

API 키는 저장소 파일이나 채팅에 입력하지 마십시오. 출금 권한이 없는 거래 전용 키를 사용하고, 빗썸에서 허용 IP를 제한한 뒤 로컬 비밀 저장소 또는 권한이 `0600`인 환경 파일로만 주입하십시오.

기본 상태에서는 `BITHUMB_NEW_ENTRIES`가 없으므로 신규 매수가 차단됩니다. 보호성 청산 감시는 계속할 수 있지만, 신규 진입 스위치는 키 회전·실계좌 상태 확인·forward 검증을 모두 마친 운영자가 직접 관리해야 합니다.

macOS에서는 `scripts/service_start.sh`가 macOS 개인정보 보호 경계 밖의 `~/Library/Application Support/BithumbCoinTrader`에 실행 코드와 최초 상태를 배치하고 LaunchAgent를 설치해 재부팅·오류 종료 후 자동 복구합니다. 이후 실행 상태와 로그의 source of truth는 이 런타임 디렉터리이며, 서로 다른 상태 파일로 같은 계좌를 제어하지 못하도록 저장소의 wrapper 직접 실행은 거부됩니다. `.env.local`은 현재 사용자 소유의 `0600` 권한이어야 하며, LaunchAgent는 wrapper에서도 신규 진입을 강제로 끕니다. 별도 주간 LaunchAgent는 매주 공개 30분봉 연구와 독립 검증만 실행하고 holdout을 열거나 실전 전략을 자동 승격하지 않습니다. 상태 확인과 중지는 각각 `scripts/service_status.sh`, `scripts/service_stop.sh`를 사용합니다.

## 검증

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
PYTHONPATH=src python3 scripts/run_wave5_research.py
PYTHONPATH=src python3 scripts/validate_wave5_research.py \
  .omx/specs/autoresearch-wave5/result.json \
  --data data/krw-btc-30m-2026-08-14-wave4.csv
PYTHONPATH=src python3 scripts/run_winrate_research.py
PYTHONPATH=src python3 scripts/validate_winrate_research.py
PYTHONPATH=src python3 scripts/run_opportunity_research.py
PYTHONPATH=src python3 scripts/validate_opportunity_research.py
PYTHONPATH=src python3 scripts/run_strategy_v2_research.py
PYTHONPATH=src python3 scripts/validate_strategy_v2_research.py
PYTHONPATH=src python3 scripts/run_strategy_v3_research.py
PYTHONPATH=src python3 scripts/validate_strategy_v3_research.py
```

Wave 5 결과는 `.omx/specs/autoresearch-wave5/`에 생성됩니다. 최신 37개 후보 연구 결과와 검증 증거는 `.omx/specs/autoresearch-winrate70/` 및 [`reports/krw-btc-winrate70-research-2026-08-24.json`](reports/krw-btc-winrate70-research-2026-08-24.json)에 생성됩니다. 기본 연구 명령은 개발 구간만 평가합니다. 후보가 개발 게이트를 통과하지 못하면 봉인 홀드아웃은 열지 않고 현금 대기를 선택합니다. 통과 후보가 생긴 경우에도 명시적 holdout 실행은 평가 전에 1회성 원장을 생성하며, 기존·크래시 상태 원장이 있으면 재실행을 거부합니다. 자세한 결론은 [`docs/WINRATE_RESEARCH_2026-08-24.md`](docs/WINRATE_RESEARCH_2026-08-24.md)를 참고하십시오.

2026-08-25 수익 우선 연구는 100,000개 정렬 봉에서 4시간 Donchian 70/30을 개발 finalist로 찾았지만, 1회성 4,000봉 holdout은 정상 청산 2건뿐이라 `INCONCLUSIVE`로 종료됐습니다. 이후 독립 감사에서 데이터 공백 뒤 4시간 지표가 새로 워밍업되지 않는 문제가 확인되어 개발 finalist와 holdout 증거를 모두 승격 불가로 무효화했습니다. 양수 최종 평가금액도 강제 최종청산의 미실현 이익 영향을 받아 통과 근거로 사용하지 않습니다. 원본 결과와 불변 해시는 [`reports/krw-btc-opportunity-research-2026-08-25.json`](reports/krw-btc-opportunity-research-2026-08-25.json), 사후 감사와 정정은 [`reports/krw-btc-opportunity-post-selection-audit-2026-08-25.json`](reports/krw-btc-opportunity-post-selection-audit-2026-08-25.json), 결론은 [`docs/OPPORTUNITY_RESEARCH_2026-08-25.md`](docs/OPPORTUNITY_RESEARCH_2026-08-25.md)에 있으며 현재 선택은 계속 `cash`입니다.

Strategy V2는 같은 30분봉 holdout을 다시 쓰지 않습니다. 공식 일봉 2,400개 중 앞 2,220개만 개발에 사용하고 마지막 180개는 해시만 기록한 채 봉인했습니다. 개발 구간의 주간 절대모멘텀 126/63 후보는 기본 비용 +47.49%, 3배 비용 +41.07%, 최대 낙폭 10.64%였지만 마지막 2개 폴드가 음수이고 PBO가 62.86%라 자동 승격 조건을 충족하지 못했습니다. 현재 실전 선택은 계속 `cash`이며 상세 근거는 [`docs/STRATEGY_V2_RESEARCH_2026-08-25.md`](docs/STRATEGY_V2_RESEARCH_2026-08-25.md), 재현 가능한 원본은 [`reports/krw-btc-strategy-v2-research-2026-08-25.json`](reports/krw-btc-strategy-v2-research-2026-08-25.json)에 있습니다.

Strategy V3는 외부 논문과 공개 구현을 더 조사해 9개 Donchian 기간 앙상블과 90일 변동성 목표 후보를 사전등록한 뒤, 목표 비중 리밸런싱과 중첩 시간순 선택을 새로 검증했습니다. 개발 직접 진단은 기본 비용 +99.50%, 최대 낙폭 12.99%로 고정 30% 보유보다 우수했지만, 외부 3개 폴드는 0.00%, +25.41%, -3.85%였고 이어 붙인 Sharpe는 0.734였습니다. 마지막 폴드 손실·낙폭·Sharpe·DSR 게이트가 실패했으므로 봉인 180일은 열지 않았고 현재 실전 선택은 계속 `cash`입니다. 상세 근거는 [`docs/STRATEGY_V3_RESEARCH_2026-08-25.md`](docs/STRATEGY_V3_RESEARCH_2026-08-25.md), 원본은 [`reports/krw-btc-strategy-v3-research-2026-08-25.json`](reports/krw-btc-strategy-v3-research-2026-08-25.json)에 있습니다.

## 안전 원칙

- 주문 POST가 타임아웃 나면 재제출하지 않고 같은 `client_order_id`를 조회합니다.
- 미확정 주문, 손상된 상태 파일, 경보·호가 조회 실패는 신규 진입을 차단합니다.
- 손실 한도와 일일 진입 한도는 보호성 전량 매도를 막지 않습니다.
- 실제 체결 손익은 OHLC 종가가 아니라 거래소 체결 원장에서 계산합니다.
- 백테스트는 데이터 갭의 첫 관측 시가에 포지션을 강제 정리하고, 최대 주문금액·KST 일일 진입 횟수를 실행 정책과 동일하게 제한합니다.
- 마지막 봉의 평가용 강제청산은 자산곡선에는 반영하지만 정규 청산 거래 수와 승률에서는 제외합니다.
- 부분매도는 요청 수량을 별도 상태로 보존하며, 상태·원장·거래소 잔고가 모두 일치한 뒤에만 완료 처리합니다.
- 모든 연구 후보와 AI 보조 결과는 별도 검증을 통과하지 않으면 라이브 전략으로 승격하지 않습니다.
- Discord 브리핑은 실제 기준자본, 신규 진입 잠금, 거래소 대조, 스캔·WebSocket 상태를 표시하며 개인 목표 금액이나 무중단 수익 표현을 사용하지 않습니다.
- 백테스트 성과는 미래 수익을 보장하지 않으며, 연구 결과는 자동으로 실전 설정을 변경하지 않습니다.
