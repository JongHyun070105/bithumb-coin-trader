# Bithumb Coin Trader — Dashboard Trading Snapshot Contract v1

## 1. 개요 (Overview)

본 문서는 Bithumb Coin Trader 프론트엔드 트레이딩 대시보드와 로컬 백엔드(오프라인 스냅샷 빌더 및 읽기 전용 로컬 API) 간의 단일 와이어 계약(Wire Contract)인 `TradingSnapshot v1` 사양을 정의합니다.

- **스키마 버전**: `schemaVersion: 1`
- **검증 규칙**: Fail-Closed (스키마 버전 누락 또는 `schemaVersion !== 1`인 경우 로딩 거부)
- **통신 제약**: Localhost(127.0.0.1 / localhost) 전용 (테스트되지 않은 ::1 바인딩 제외), 읽기 전용(GET 전용), 외부 네트워크 차단
- **CORS 보안**: 루프백 오리진(`http://localhost:<port>`, `http://127.0.0.1:<port>`)만 허용. 와일드카드 `Access-Control-Allow-Origin: *` 절대 금지. 외부 오리진 프리플라이트 요청은 403 Forbidden 거부.

---

## 2. Null 및 수치 시맨틱 (Null vs Zero Semantics)

- **`null`**: 알 수 없음(Unknown) 또는 현재 데이터 소스에서 제공되지 않음(Unavailable).
  - 예: 과거 자산 이력이 없어 MDD 계산이 불가능한 경우 `maxDrawdown: null`
  - 예: 일별 베이스라인이 없거나 KST 기준일 불일치 시 `todayPnl: null`, `todayReturnPct: null`
  - 예: 개별 체결(Fill)만 존재하고 완결된 왕복 거래(Round-trip Trade) 원장이 없을 때 `recentTrades: []`, `winRate: null`, `profitFactor: null`, `averageTrade: null`
  - 예: `FillLedger`가 매도 후 진입 수수료 분해를 노출하지 않는 경우 `entryFee: null`
- **`0`**: 수치적으로 확인된 0 (Confirmed Zero).
  - 예: 당일 외부 순입출금이 없는 경우 `netCashFlow: 0`
  - 예: 에러 카운트가 0건인 경우 `errors: 0`
- **금지 사항**: 알 수 없거나 누락된 값을 화면이나 API에서 자의적으로 0으로 치환하지 않는다.

---

## 3. 데이터 출처 시맨틱 (Source Semantics)

스냅샷의 `source.kind`는 데이터의 인식론적 신뢰 수준(Provenance)을 엄격히 구분합니다.

| `source.kind` | 설명 | 대시보드 상태 (`TradingDataStatus`) | 비고 |
| :--- | :--- | :--- | :--- |
| `synthetic` | 시연 및 UI 테스트용 합성 데이터 | `SYNTHETIC_DEMO` | "SYNTHETIC DATA" 배지 표시 |
| `local_snapshot` | 로컬 파일 임포트 또는 오프라인 FillLedger 어댑터 출력 | `LOCAL_SNAPSHOT` 또는 `READ_ONLY_API` | 권위 있는(authoritative) 데이터로 승격 불가 |
| `authoritative` | 검증된 거래소 파이프라인의 실데이터 | `REAL_DATA` | 엄격한 권위 검증 통과 시에만 허용 |

> **중요**: 로컬 API(`/api/trading/snapshot`)를 통해 서빙되더라도 페이로드가 `local_snapshot`인 경우 대시보드는 이를 `authoritative`나 `REAL_DATA`로 승격하지 않고 `READ_ONLY_API` 상태 및 `local_snapshot` 출처를 유지합니다.

---

## 4. 최상위 스키마 구조 (Top-Level Schema)

```typescript
export interface TradingSnapshot {
  schemaVersion: 1
  timestamp: string // ISO 8601 UTC
  mode: 'OFF' | 'PAPER' | 'LIVE'
  source: {
    kind: 'synthetic' | 'authoritative' | 'local_snapshot'
    label: string
  }
  portfolio: PortfolioSummary
  positions: Position[]
  recentTrades: Trade[]
  equityCurve: EquityPoint[]
  dailyPerformance: DailyPerformance[]
  botStatus: BotStatus
  performance: PerformanceMetrics
  today: TodayMetrics
  dailyBaseline: DailyBaseline | null
}
```

---

## 5. 하위 객체 상세 규격

### 5.1 포트폴리오 (`portfolio: PortfolioSummary`)
- `equity`: 총 평가 자산 (KRW). `cash + exposure`와 일치해야 함.
- `cash`: 보유 원화 현금 (KRW).
- `exposure`: 보유 가상자산 총 평가액 (KRW).
- `todayPnl`: 당일 손익 (KRW, `dailyBaseline` 기반 계산).
- `todayReturnPct`: 당일 수익률 (%, `dailyBaseline` 기반 계산).
- `totalPnl`: 총 손익 (KRW, 시작 자산 대비).
- `totalReturnPct`: 총 수익률 (%).
- `realizedPnl`: 실현 손익 (KRW, 체결 원장 기준 누적).
- `unrealizedPnl`: 미실현 손익 (KRW, 현재 마크가 기준).
- `fees`: 누적 지불 수수료 (KRW). 순손익에 이미 반영되어 있으므로 중복 차감하지 않음.

### 5.2 포지션 (`positions: Position[]`)
- `id`: 고유 식별자 (string).
- `asset`: 자산 기호 (예: "BTC").
- `name`: 자산명 (예: "비트코인").
- `pair`: 마켓 페어 (예: "KRW-BTC").
- `side`: 항상 `'LONG'`.
- `entry`: 평균 매수가 (KRW).
- `current`: 현재 마크가 (KRW).
- `quantity`: 보유 수량.
- `exposure`: 평가금액 (`quantity * current`).
- `pnl`: 미실현 손익 (`(current * quantity) - cost_basis`). 진입 수수료 반영, 미실행 청산 수수료 미가정.
- `pnlPct`: 미실현 수익률 (%).
- `entryFee`: 해당 포지션에 할당된 기지불 진입 수수료 (number | null). `FillLedger`는 마켓 전체의 누적 수수료(청산/매도 수수료 포함)를 관리하므로, 매도 발생 후 잔여 진입 수수료를 임의 추정하지 않고 `null`로 표기함.
- `openedAt`: 현재 보유 포지션 사이클의 진입 시각 (ISO 8601). 전량 매도(0 수량) 후 재매수한 경우 새 사이클의 시각을 반영.
- `strategy`: 전략 식별자 또는 null.

### 5.3 최근 거래 (`recentTrades: Trade[]`)
- 개별 체결(Fill)과 완결된 왕복 거래(Trade)는 엄격히 구분됨.
- 완결된 왕복 거래 원장이 제공되지 않는 경우 빈 배열(`[]`)로 유지하며, 임의로 체결을 묶어 거래로 날조하지 않음.

### 5.4 봇 상태 (`botStatus: BotStatus`)
- `mode`: `'OFF' | 'PAPER' | 'LIVE'`
- `strategy`: 운용 중인 전략명 또는 null.
- `marketData`: `'PENDING' | 'READY'` (미제공 시 `'PENDING'`)
- `orderExecution`: `'DISABLED' | 'PAPER' | 'LIVE'` (미제공 시 `'DISABLED'`)
- `riskGuard`: `'LOCKED' | 'ACTIVE'` (미제공 시 `'LOCKED'`)
- `lastActivity`: 마지막 활동 시각 (ISO 8601) 또는 null (미제공 시 `null`).
- `uptimeSeconds`: 가동 시간 (초) 또는 null (미제공 시 `null`, 음수 불가).
- `todayTrades`: 당일 거래 횟수 또는 null (미제공 시 `null`, 음수 불가).
- `errors`: 에러 카운트 또는 null (미제공 시 `null`, 음수 불가).

### 5.5 일별 베이스라인 (`dailyBaseline: DailyBaseline | null`)
- 당일 KST(Asia/Seoul) 00:00:00 기준 시작 자산 및 순외부입출금 데이터.
```typescript
export interface DailyBaseline {
  equity: number | null
  netCashFlow: number | null
  tradingDay: string // YYYY-MM-DD
  timeZone: 'Asia/Seoul'
}
```
- 당일 손익 불변식:
  $$\text{todayPnl} = \text{equity} - \text{baseline.equity} - \text{baseline.netCashFlow}$$
  $$\text{todayReturnPct} = \frac{\text{todayPnl}}{\text{baseline.equity}} \times 100$$
- `baseline.tradingDay`가 스냅샷 시점의 서울 기준일과 불일치하거나 베이스라인이 누락된 경우 당일 지표는 `null`이 됨.

---

## 6. 입력 데이터 신선도 및 회계 진실 불변식

1. **보수적 스냅샷 신선도 (Freshness Rule)**:
   - `snapshot.timestamp = min(account_state.timestamp, mark_prices.timestamp)`
   - 계좌 잔고가 최신이더라도 마크 가격이 과거 데이터인 경우 스냅샷 타임스탬프는 마크 가격의 시각을 보수적으로 반영하여 거짓 신선도를 방지함.
2. **회계 진실 우선 원칙 (Accounting Truth)**:
   - `equity_history` 파일이 제공되더라도 계좌 잔고와 체결 원장에서 산출된 `portfolio.totalReturnPct`가 `performance.totalReturn`보다 우선하며 덮어쓰지 않음.
   - 왕복 거래 원장이 없는 경우 `winRate`, `profitFactor`, `averageTrade`는 엄격히 `null`로 유지됨.
