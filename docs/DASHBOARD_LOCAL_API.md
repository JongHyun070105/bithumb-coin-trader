# 로컬 읽기 전용 대시보드 API & 오프라인 스냅샷 빌더 (v1)

이 문서는 Bithumb Coin Trader의 오프라인 `FillLedger` 원장과 프론트엔드 대시보드(v0.3)를 안전하게 연결하는 **로컬 읽기 전용 HTTP API 서버** 및 **오프라인 스냅샷 빌더**의 아키텍처, 사용법, 보안 경계를 설명합니다.

---

## 1. 아키텍처 개요

```
[오프라인 체결 원장]
fills.jsonl ────────┐
계좌 잔고 (JSON) ───┼──> dashboard_snapshot.py ──> trading_snapshot.json (v1)
현재가 마크 (JSON) ─┘                                     │
                                                          ▼
                                              dashboard_api.py (포트 8765)
                                              [127.0.0.1 전용, 읽기 전용]
                                                          │
                                         HTTP GET         ▼
                                      ─────────────> 웹 대시보드 (Vite: 4177)
                                      <───────────── [READ_ONLY_API 모드]
```

### 1.1 핵심 보안 불변식 (Fail-Closed Boundaries)
1. **로컬호스트 바인딩 강제**: `127.0.0.1`, `localhost` 바인딩만 허용하며, `0.0.0.0` 또는 외부 IP 바인딩 시 즉시 시작 실패(`DashboardApiError`). (테스트되지 않은 ::1 바인딩 제외)
2. **읽기 전용 (Read-Only)**: `GET` 및 `OPTIONS` 메소드만 허용하며, 모든 변경 요청(`POST`, `PUT`, `PATCH`, `DELETE`)은 `405 Method Not Allowed`를 반환합니다.
3. **CORS 데이터 유출 방지 (Loopback Only)**:
   - 루프백 오리진(`http://localhost:<port>`, `http://127.0.0.1:<port>`)만 허용하며, 요청된 정확한 오리진을 `Access-Control-Allow-Origin`으로 에코합니다.
   - 와일드카드 `Access-Control-Allow-Origin: *`는 절대 사용하지 않습니다.
   - 모든 응답에 `Vary: Origin` 헤더를 포함합니다.
   - 외부 웹 오리진(예: `http://evil.example`)의 프리플라이트(`OPTIONS`) 요청은 명시적으로 `403 Forbidden`으로 거부됩니다.
   - `Origin` 헤더가 없는 로컬 CLI 도구(curl 등)는 정상 동작합니다.
4. **출처 격리 (Provenance Isolation)**:
   - 로컬 API를 통해 수신된 데이터는 `READ_ONLY_API` 상태로 격리됩니다.
   - `source.kind`는 스냅샷의 원래 출처(`local_snapshot`)를 보존하며, 절대 `REAL_DATA`나 `authoritative`로 승격되지 않습니다.
5. **실거래 통로 완전 부재**: 주문 발주 API, 사설 거래소 키 설정, 외부 클라우드(AWS 등) 통신 경로가 일체 존재하지 않습니다.
6. **동시성 안전 (Thread Safety)**: `SnapshotCache`는 내부 뮤텍스 락(`threading.Lock`)을 통해 멀티스레드 환경에서 안전하게 동작합니다.

---

## 2. 오프라인 스냅샷 빌더 (`dashboard_snapshot.py`)

오프라인 체결 기록(`FillLedger`), 계좌 잔고, 마크 가격을 결합하여 대시보드 v1 규격의 `trading_snapshot.json`을 생성합니다.

### 2.1 주요 기능 및 회계 원칙
- **정밀한 Decimal 회계**: 잔고, 포지션 평가액, 손익 계산 시 부동소수점 오차 없이 정확한 `Decimal` 연산 수행.
- **자본 보존 불변식**: `총 자본 = 현금 잔고 + 포지션 총 평가액`
- **미실현 손익 계산**: `(현재 마크 가격 - 평균 매수가) * 보유 수량`
- **실현 손익 / 수수료**: `FillLedger`에 집계된 수치를 직접 인용.
- **수량 0 포지션 자동 제외**: 청산 완료된 자산은 오픈 포지션 목록에서 제외.
- **진입 수수료 정직한 표기 (`entryFee: null`)**: `FillLedger`는 마켓 전체의 누적 수수료를 집계하므로 매도 후 남은 진입 수수료를 분해/날조하지 않고 `null`을 반환합니다.
- **포지션 진입 시각 (`openedAt`)**: 현재 포지션 사이클의 진입 시각을 반영(전량 청산 후 재진입 시 새 사이클 시각 적용).
- **입력 신선도 (`timestamp`)**: `min(account_state.timestamp, mark_prices.timestamp)` 규칙을 적용하여 과거 마크 가격으로 인한 거짓 신선도를 방지합니다.
- **비관적 봇 상태 기본값**: 계좌 상태에서 `botStatus`가 명시되지 않은 경우 `marketData="PENDING"`, `orderExecution="DISABLED"`, `riskGuard="LOCKED"`, `lastActivity=null` 등 비관적 기본값을 사용합니다.
- **체결(Fill)과 왕복 거래(Trade) 구분**: 오프라인 체결 기록을 자의적으로 묶어 거래로 날조하지 않고 `recentTrades: []`, `winRate: null`, `profitFactor: null`, `averageTrade: null`을 반환합니다.
- **빌더 자체 검증 (Self-Validation)**: 파일 작성 직전 `validate_trading_snapshot`을 실행하여 결함이 있는 스냅샷 파일의 생성을 사전 차단합니다.
- **기술적 부채 고지 (TECHNICAL_DEBT / ASTRA_REVIEW_CANDIDATE)**: `FillLedger`가 원장 레코드 열람 공개 인터페이스를 제공하지 않아 `ledger._load()`를 호출하여 사이클 진입 시각을 산출합니다.

### 2.2 CLI 사용법
```bash
# 기본 픽스처로 스냅샷 생성
python3 -m bithumb_coin_trader.dashboard_snapshot \
  --ledger examples/dashboard/fills.demo.jsonl \
  --account-state examples/dashboard/account_state.demo.json \
  --marks examples/dashboard/mark_prices.demo.json \
  --equity-history examples/dashboard/equity_history.demo.json \
  --output trading_snapshot.json
```

---

## 3. 로컬 읽기 전용 API 서버 (`dashboard_api.py`)

Python 표준 라이브러리(`http.server.ThreadingHTTPServer`) 기반 경량 REST 서버입니다.

### 3.1 엔드포인트 사양
| 엔드포인트 | 설명 | 응답 형식 |
|---|---|---|
| `GET /api/health` | 서버 상태, 버전, 스냅샷 유효성 및 가용 여부 | JSON |
| `GET /api/trading/snapshot` | 전체 `TradingSnapshot v1` 데이터 (유효하지 않을 시 503) | JSON |
| `GET /api/portfolio` | 포트폴리오 요약 정보 | JSON |
| `GET /api/positions` | 오픈 포지션 목록 | JSON |
| `GET /api/trades` | 최근 거래 내역 (`[]`) | JSON |
| `GET /api/performance` | 성과 분석 지표 | JSON |
| `GET /api/bot/status` | 봇 실행 상태 및 안전 게이트 | JSON |

### 3.2 서버 실행
```bash
# 기본 포트(8765)로 실행
PYTHONPATH=src python3 -m bithumb_coin_trader.dashboard_api \
  --snapshot examples/dashboard/trading_snapshot.demo.json \
  --port 8765
```

---

## 4. 프론트엔드 대시보드 연동

1. **상단 컨트롤 버튼**:
   - 우측 상단 `로컬 API 연결` 버튼 클릭 -> `http://127.0.0.1:8765`로부터 스냅샷을 가져와 화면에 반영.
   - 연결 성공 시 파란색 `로컬 API 연결됨` 배지 표시.
   - `로컬 API 연결 해제` 클릭 시 즉시 초기 `NO_DATA` 상태로 복귀.
2. **URL 파라미터 자동 연결**:
   - 브라우저에서 `http://127.0.0.1:4177/?source=api#dashboard` 접속 시 마운트 즉시 로컬 API에 자동 연결.
3. **오래된 데이터(Staleness) 감지**:
   - 스냅샷 생성 시각이 현재 시각 기준 60초를 초과하면 주황색 `데이터 오래됨 (60초 초과)` 경고 배지로 자동 전환.
4. **오류 배너**:
   - API 연결 실패, 스냅샷 파싱/계약 검증 실패 시 상단에 상세 오류 메시지가 포함된 닫기 가능한 배너 노출.

---

## 5. 골든 픽스처 및 교차 언어 계약 검증

- 골든 픽스처: `dashboard/tests/golden/python_trading_snapshot_v1.json`
- Python 검증 모듈: `src/bithumb_coin_trader/dashboard_contract.py`
- TypeScript 검증 모듈: `dashboard/src/trading/snapshotValidation.ts`
- 교차 언어 테스트: `dashboard/src/trading/crossLanguageContract.test.ts`
