# Market Data Normalization Contract

## 1. 개요 (Overview)

본 문서는 `bitcoin-trader` 수집 파이프라인에서 수집 및 정규화되는 3대 거래소(Bithumb, Binance, Upbit)의 시장 데이터(Orderbook, Trade, Ticker)에 대한 표준 정규화 규약(Normalization Contract)을 정의한다.
다운스트림 특징 엔지니어링(Feature Engineering), 오프라인 체결 시뮬레이터(Execution Simulator), 리플레이 엔진이 단일화된 인터페이스를 통해 데이터를 안전하고 일관되게 소비할 수 있도록 보장한다.

---

## 2. 공통 데이터 모델 및 타입 규약 (Common Data Models)

모든 정규화된 레코드는 아래의 필수 공통 필드를 포함하며, JSON Lines 및 Parquet 포맷과 100% 호환된다.

### 2.1 타임스탬프 규약 (Timestamp Conventions)

| 필드명 | 타입 | 단위 | 설명 |
| :--- | :--- | :--- | :--- |
| `timestamp` | `int64` | milliseconds (`ms`) | 거래소 엔진이 부여한 이벤트 발생 시각 (Exchange Matching Engine Timestamp). 거래소 미제공 시 수신 시각으로 대체되거나 별도 표기됨. |
| `received_at` | `int64` | milliseconds (`ms`) | 수집기 인스턴스(EC2)에서 웹소켓 프레임을 최초 수신 및 역직렬화한 로컬 시각 (Wall-clock POSIX Epoch ms). |
| `sequence_id` | `int64` / `null` | integer | 거래소에서 제공하는 단조 증가 시퀀스 번호 (미제공 시 `null`). |

- **시계 정렬 원칙**: 특징 계산 및 인과성(Causality) 검증 시에는 원칙적으로 `received_at`을 기준으로 정렬하여 룩어헤드(Lookahead) 편향을 원천 차단한다.
- **레이턴시 측정**: `latency_ms = received_at - timestamp`. 음수 레이턴시(Local clock drift) 발생 시 엄격 감사 플래그가 기록된다.

---

## 3. 엔티티별 정규화 명세 (Entity Specifications)

### 3.1 Orderbook (오더북 / 호가창)

- **토픽/채널**: `orderbook`
- **스냅샷 정책**: L2 깊이(Depth) 고정 스냅샷 (Top 30 또는 Top 50 레벨). 델타 업데이트는 링버퍼에서 통합된 전체 스냅샷으로 정규화됨.

#### 표준 스키마
```json
{
  "exchange": "bithumb" | "binance" | "upbit",
  "symbol": "BTC-KRW" | "BTCUSDT",
  "timestamp": 1757001600123,
  "received_at": 1757001600145,
  "sequence_id": 10849201,
  "bids": [
    [85000000.0, 0.4512],
    [84999000.0, 1.2050]
  ],
  "asks": [
    [85001000.0, 0.3201],
    [85002000.0, 2.1500]
  ],
  "depth": 30
}
```

#### 필드 상세
- `bids`: `[[price, volume], ...]` 내림차순 정렬 (Best Bid = index 0). 가격과 수량은 64비트 부동소수점 (`float64`).
- `asks`: `[[price, volume], ...]` 오름차순 정렬 (Best Ask = index 0). 가격과 수량은 64비트 부동소수점 (`float64`).
- **불변식(Invariant)**: `asks[0][0] > bids[0][0]` (Crossed book 발생 불가, 발생 시 `CROSSED_BOOK` 에러 플래그 부여).

---

### 3.2 Trade (체결 내역)

- **토픽/채널**: `trade`
- **실시간성**: 개별 체결 이벤트 단위 스트리밍.

#### 표준 스키마
```json
{
  "exchange": "bithumb" | "binance" | "upbit",
  "symbol": "BTC-KRW" | "BTCUSDT",
  "trade_id": "184920481",
  "timestamp": 1757001600120,
  "received_at": 1757001600135,
  "side": "buy" | "sell",
  "price": 85001000.0,
  "volume": 0.0451,
  "turnover": 3833545.1
}
```

#### 사이드(Side) 규약
- `side`: **테이커(Taker / Aggressor)의 매수/매도 방향** 기준.
  - `"buy"`: 테이커가 매수 호가(Ask)를 타격하여 체결됨 (Buyer is taker, upward pressure).
  - `"sell"`: 테이커가 매도 호가(Bid)를 타격하여 체결됨 (Seller is taker, downward pressure).
- `turnover`: `price * volume` (체결 대금).

---

### 3.3 Ticker (현재가 / 24시간 통계 요약)

- **토픽/채널**: `ticker`
- **주기**: 거래소 이벤트 푸시 기반 (변동 시 전송).

#### 표준 스키마
```json
{
  "exchange": "bithumb" | "binance" | "upbit",
  "symbol": "BTC-KRW" | "BTCUSDT",
  "timestamp": 1757001600120,
  "received_at": 1757001600140,
  "last_price": 85001000.0,
  "open_price_24h": 84200000.0,
  "high_price_24h": 85500000.0,
  "low_price_24h": 83900000.0,
  "volume_24h": 1250.451,
  "turnover_24h": 106288335000.0,
  "change_rate_24h": 0.0095
}
```

---

## 4. 거래소별 상세 매핑 및 주의점 (Exchange-Specific Nuances)

### 4.1 Bithumb (빗썸)

1. **심볼 표기법**: 내부 원천 심볼은 `BTC_KRW` 형태이며, 정규화 시 `BTC-KRW`로 변환.
2. **타임스탬프 정밀도**: 
   - 빗썸 웹소켓 JSON의 `datetime` 필드는 마이크로초 단위 정밀도 문자열(`"1757001600123456"`)로 제공되거나 밀리초 정밀도로 제공됨.
   - 파서에서 `int(ts[:13])`을 통해 표준 `ms` 타임스탬프로 결정론적 절삭 정규화.
3. **체결 ID (`trade_id`) 부재**:
   - 빗썸 구형 체결 웹소켓은 고유한 `trade_id`를 발행하지 않음.
   - 정규화 엔진에서 `synthetic_trade_id = sha256(timestamp:price:volume:side)[:16]`를 생성하여 멱등성 및 중복 검사에 사용.
4. **오더북 스냅샷 크기**: 기본 30레벨 제공.

### 4.2 Binance (바이낸스)

1. **심볼 표기법**: 소문자 무구분자 `btcusdt` -> 대문자 `BTCUSDT`.
2. **사이드 표기 매핑**:
   - 바이낸스 체결 JSON의 `m` (isBuyerMaker) 필드:
     - `m == true`: 매수자가 메이커 -> 테이커는 매도자 -> `side = "sell"`.
     - `m == false`: 매수자가 테이커 -> `side = "buy"`.
3. **타임스탬프**: `E` (Event time) 및 `T` (Trade time) 밀리초 정수형. 표준 `timestamp = T`.
4. **체결 ID**: `t` (Trade ID) 정수형 -> 문자열 `trade_id = str(t)`.
5. **오더북 깊이**: `<symbol>@depth20@100ms` 또는 `@depth` 스트림 사용 시 탑 20~50 레벨 스냅샷.

### 4.3 Upbit (업비트)

1. **심볼 표기법**: `KRW-BTC` -> 정규화 시 `BTC-KRW` 또는 거래소별 베이스-쿼트 표준에 맞춰 변환.
2. **사이드 표기 매핑**:
   - `ask_bid`: `"ASK"` -> 테이커 매도 -> `side = "sell"`.
   - `ask_bid`: `"BID"` -> 테이커 매수 -> `side = "buy"`.
3. **타임스탬프**: `trade_timestamp` (ms) 및 `trade_time` (KST/UTC). 정규화 시 `trade_timestamp` 사용.
4. **순차 체결 번호**: `sequential_id` 제공 -> `trade_id = str(sequential_id)`.
5. **스트림 압축**: 기본 바이너리 포맷 지원하나 JSON 포맷 구독 유지.

---

## 5. 검증 및 불변식 검사 (Validation & Invariants)

정규화 엔진은 각 레코드 변환 시 다음의 조건을 엄격히 검증하며 위반 시 격리 큐(Quarantine)로 라우팅한다.

- `FiniteNumeric`: 가격, 수량, 턴오버는 `NaN`, `+Inf`, `-Inf`가 아니어야 하며 `0.0`을 초과해야 함 (`> 0`).
- `ValidSide`: `side`는 반드시 `"buy"` 또는 `"sell"`이어야 함.
- `TimestampBounds`: `timestamp <= received_at + 10000` (클럭 왜곡 10초 이내 허용, 미래 타임스탬프 차단).
- `OrderbookMonotonicity`:
  - `bids[i][0] > bids[i+1][0]` (내림차순)
  - `asks[i][0] < asks[i+1][0]` (오름차순)
  - `bids[0][0] < asks[0][0]` (스프레드 양수)
