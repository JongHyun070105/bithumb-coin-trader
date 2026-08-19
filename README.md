# Bithumb Coin Trader (Autonomous Multi-Agent Quant System)

빗썸(Bithumb) KRW 현물 시장을 대상으로 **Tauric 멀티에이전트 아키텍처**, **기관 수급 캔들 디스플레이스먼트(Institutional Displacement)**, 그리고 **실시간 30호가창 불균형(Orderbook Imbalance)**을 결합한 24시간 자율 트레이딩 시스템입니다.

---

## 🏛️ 시스템 아키텍처 (Pixel Trading Floor)

본 시스템은 정적 지표 하나에 의존하지 않고, 4개 전문 분석실과 2개 리스크 위원회, 그리고 최종 포트폴리오 매니저(PM) 파이프라인을 거쳐 매매를 집행합니다.

```text
 ┌─────────────────────────────────────────────────────────────┐
 │               📡 Bithumb Realtime 10-Market Scanner          │
 └──────────────────────────────┬──────────────────────────────┘
                                │ (30s Cycle)
 ┌──────────────────────────────▼──────────────────────────────┐
 │                  🏢 PIXEL TRADING FLOOR                     │
 │  ├─ 👨‍💻 TARO  (Technical) : MA(20/50/100), Wilder RSI, MACD │
 │  ├─ 👩‍💼 DIANA (Institutional): 50%+ Body & 2.0x Vol Spike     │
 │  ├─ 🚀 NOVA  (Momentum)   : 20-bar Trend Velocity Factor    │
 │  └─ 🧘 VIBE  (Sentiment)  : 30-Orderbook Imbalance & BB     │
 └──────────────────────────────┬──────────────────────────────┘
                                │
 ┌──────────────────────────────▼──────────────────────────────┐
 │            ⚔️ Research Room: BULL vs BEAR Debate             │
 └──────────────────────────────┬──────────────────────────────┘
                                │
 ┌──────────────────────────────▼──────────────────────────────┐
 │     🛡️ Risk Committee: SAFE Bounds & Bithumb Warning Guard   │
 └──────────────────────────────┬──────────────────────────────┘
                                │
 ┌──────────────────────────────▼──────────────────────────────┐
 │        👔 ACE & PM Decision Gate: Dynamic Capital Sizing     │
 └──────────────────────────────┬──────────────────────────────┘
                                │
 ┌──────────────────────────────▼──────────────────────────────┐
 │     🚀 Bithumb Official MCP Execution (3s Price / SL / TP)   │
 └─────────────────────────────────────────────────────────────┘
```

### 1. 4대 애널리스트 룸
- **👨‍💻 TARO (Technical Analyst)**: 이평선 정배열(골든크로스), 와일더 RSI(40~70 모멘텀 존), MACD 히스토그램 0선 상향 반전 추적.
- **👩‍💼 DIANA (Institutional & Volume Delta)**: 캔들 몸통 비율(Displacement $\ge 50\%$)과 직전 20봉 평균 대비 2.0배 이상의 거래량 스파이크(Bullish Shift) 감지.
- **🚀 NOVA (Momentum Engine)**: 최근 20봉의 가격 변화율과 방향성 가속도 측정.
- **🧘 VIBE (Orderbook & Sentiment)**: 볼린저 밴드 변동성 스퀴즈 및 빗썸 실시간 30호가 잔량 비율(Bid-Ask Depth Imbalance) 가산점 부여.

### 2. 리스크 위원회 & 빗썸 세이프가드
- **SAFE Gate**: 과매수(RSI > 72) 진입 금지, 하락 기관 시프트(Bearish Shift) 발생 시 매수 차단.
- **Bithumb Warning Filter (`market_get_warnings`)**: 거래소 투자유의/투자경보 지정 코인 즉시 스캔 제외.
- **Bithumb Notices Detector (`market_get_notices`)**: 상장, 입출금 중단 등 중요 이벤트 실시간 로깅.

---

## 📈 자금 관리 및 복리 운용 원칙 (Capital Management)

- **비대칭 손익비 (Asymmetric Risk-Reward)**:
  - **손절선 (Stop-Loss)**: **-2.0%** (3초 내 즉각적인 칼손절)
  - **1차 목표가 (Take-Profit)**: **+4.0%** (빠른 회전율 기반 복리 사이클)
  - **트레일링 스탑 (Trailing-Stop)**: 고점 대비 **-1.5%** (추세 확장 시 초과 수익 확보)
- **다이내믹 포지션 사이징 (Dynamic Sizing)**:
  - 초강력 셋업 (확신도 $\ge 80\%$ + 호가 매수벽 $\ge 55\%$): 가용 자본의 **60%** 투입
  - 일반 우량 셋업 (확신도 $70\%\sim 79\%$): 가용 자본의 **40%** 투입
  - 나머지 자본은 비상 현금 버퍼(Cash Buffer)로 유지하여 급격한 시장 변동성 방어.
- **복리 스노우볼 (Compounding Acceleration)**:
  - 1회 +4.0% 익절 사이클을 반복 누적하여 단계별 자산 퀀텀 점프 달성.

---

## 📱 실시간 모바일 관제 (Discord Integration)

로컬 게이트웨이를 통해 `finance-chat` 채널로 실시간 리치 알림을 자동 전송합니다.

1. **🟢 매수 진입 알림**: 종목, 체결단가, AI 확신도, 호가 매수벽 비율, 1차 목표가/손절선.
2. **🔴 포지션 청산 알림**: 실현 손익률(%), 실현 손익금, 청산 사유(TP/SL/Trailing), 누적 P&L.
3. **📊 정기 1시간 포트폴리오 브리핑**: 총 자산, 가용 현금, 보유 포지션 실시간 PnL, 10대 코인 랭킹 Top 3.

---

## 🛠️ 설치 및 실행

### 요구사항
- Python 3.11+
- Node.js 18+ (공식 `@bithumb-official/bithumb-mcp` 연동)

### 설치
```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

### 환경변수 설정
```bash
export BITHUMB_ACCESS_KEY="your_access_key"
export BITHUMB_SECRET_KEY="your_secret_key"
export BITHUMB_LIVE_TRADING="true"
export TRADING_MODE="live"
```

### 24시간 자율 트레이딩 데몬 실행
```bash
.venv/bin/python scripts/autonomous_trader.py
```

### 10대 코인 실시간 스캔 단독 실행 (Dry-run)
```bash
.venv/bin/python scripts/scan_and_trade.py
```

---

## 🔒 보안 및 리스크 고지
- 모든 API 키는 저장소에 커밋되지 않으며 환경변수로만 주입됩니다.
- 본 시스템은 손실 방지를 위한 fail-closed 원칙을 준수하며, 가상자산 투자의 최종 책임은 사용자 본인에게 있습니다.
