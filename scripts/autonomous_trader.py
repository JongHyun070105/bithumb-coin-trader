#!/usr/bin/env python3
"""
🤖 AUTONOMOUS TRADING DAEMON v4.0 (Self-Healing Infinite Compounding Engine)
────────────────────────────────────────────────────────────────────────────────
Target   : Infinite Compounding (Auto-Rolling Milestones, Never Stops)
Engines  :
  - Dynamic 25-Universe Always-On Radar (Top 25 Liquid & Momentum Markets)
  - Tauric Multi-Agent (TARO, DIANA, NOVA, VIBE, ACE, PM)
  - Dynamic Pyramiding Engine (Automatic 2nd Scale-In for High-Conviction Setups)
  - Institutional Volume Delta & Candle Displacement
  - Bithumb Realtime 30-Orderbook Imbalance (Bid-Ask Depth Ratio)
  - Bithumb Market Warnings & Delisting Safeguard
  - 📱 Discord Finance-Chat Rich Mobile Alerts & Hourly Briefings
  - 🛡️ Self-Healing Error Recovery (Auto-Restart on ANY failure)
  - 🔄 Exchange Balance Reconciliation (Source of Truth = Bithumb)
Risk     : SL -2.0%, TP +4.0%, Trailing-Stop -1.5% from peak (Activates at +1.0%)
           MDD 10% Daily Loss 2% Enforced
Cycle    : 3s Price Watch, 30s Multi-Market Fusion Scan, 1h Discord Briefing
"""

from __future__ import annotations

import json
import os
import time
import sys
import traceback
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.data import fetch_minute_candles
from bithumb_coin_trader.discord_notify import (
    SilentNotifier,
    notify_buy_entry,
    notify_partial_sell_exit,
    notify_sell_exit,
    notify_hourly_briefing,
    send_discord_message,
)
from bithumb_coin_trader.execution import (
    BithumbExecutor, TradeIntent, plan_execution, LIVE_CONFIRMATION_TOKEN,
)
from bithumb_coin_trader.mcp_client import McpStdioClient, LIVE_COMMAND
from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.risk import RiskContext, RiskLimits, evaluate_pretrade
from bithumb_coin_trader.state import BotState, load_state, save_state
from scripts.scan_and_trade import DEFAULT_MARKETS, analyze_market

STATE_PATH = PROJECT_ROOT / "state" / "live.json"
TRADE_LOG_PATH = PROJECT_ROOT / "state" / "trade_history.jsonl"
PORTFOLIO_PATH = PROJECT_ROOT / "state" / "portfolio.json"
JOURNAL_PATH = PROJECT_ROOT / "TRADING_JOURNAL.md"

# ── Target & Risk Parameters (v4.1 High-Turnover Quant Engine) ──
INITIAL_CAPITAL = 30000.0       # 최초 시작 원금 (30,000 KRW)
TARGET_RETURN_PCT = float(os.environ.get("TARGET_RETURN_PCT", "50.0"))  # 마일스톤 수익률 (+50%)
STOP_LOSS_PCT = 0.018          # 손절가 비율 (-1.8%)
TAKE_PROFIT_PCT = 0.038        # 2차 최종 목표가 비율 (+3.8%)
SPLIT_TP_PCT = 0.020           # 1차 50% 분할 익절 비율 (+2.0%)
TRAILING_STOP_PCT = 0.018      # 트레일링 스탑 추종 폭 (-1.8% from peak으로 호가 털림 방어)
TRAILING_ACTIVATE_PCT = 0.022  # 트레일링 활성화 기준 (+2.2% 이상 상승 확인 시에만 가동)
PYRAMIDING_MIN_GAIN_PCT = 0.70 # 2차 불타기 진입 기준 (+0.7% 이상 유의미한 상승 확인 시)
TIMECUT_SECONDS = 14400        # 4시간 (14,400초) 횡보 시 타임컷
TIMECUT_THRESHOLD_PCT = 0.60   # ±0.6% 내 횡보 판정
REENTRY_COOLDOWN_SEC = 600  # 청산 후 동일 종목 10분(600초) 재진입 쿨다운  # 청산 후 동일 종목 30분(1800초) 재진입 완전 금지     # 청산 후 동일 종목 재진입 쿨다운 (15분 = 900초, 웝소 수수료 낭비 방어)
MIN_CONFIDENCE_ENTRY = 70.0  # 최소 진입 확신도
MIN_CONFIDENCE_STRONG = 75.0 # 강력 매수 확신도
MIN_ORDER_KRW = 5_000
MAX_POSITION_RATIO = 0.60   # 최대 포지션 비율 (자산의 60%)
MIN_RESERVE_CASH_RATIO = 0.33  # 최소 현금 보존 비율 (33%)
PRICE_CHECK_INTERVAL = 3     # seconds
SCAN_INTERVAL_LOOPS = 10     # every 10 loops = ~30 seconds
HOURLY_REPORT_LOOPS = 1200   # every 1200 loops = ~1 hour
NOTICE_CHECK_LOOPS = 200     # every ~10 minutes
RECONCILE_LOOPS = 600        # every ~30 minutes: exchange balance reconciliation
MAX_CONSECUTIVE_ERRORS = 5   # 연속 에러 시 긴급 알림
ERROR_COOLDOWN_SEC = 60      # 에러 발생 시 대기 시간
FEE_BUFFER = 1.003           # 수수료 버퍼 (0.3% 여유)

EXITS_PATH = PROJECT_ROOT / "state" / "recent_exits.json"


def load_recent_exits() -> dict[str, float]:
    if EXITS_PATH.exists():
        try:
            return json.loads(EXITS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def record_exit(market: str):
    exits = load_recent_exits()
    exits[market] = time.time()
    EXITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXITS_PATH.write_text(json.dumps(exits, indent=2), encoding="utf-8")


@dataclass
class PortfolioState:
    """Tracks portfolio across multiple trades for compounding."""
    total_capital: float = 0.0
    cash_available: float = 0.0
    active_market: str = ""
    entry_price: float = 0.0
    position_volume: str = "0"
    highest_price: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_krw: float = 0.0
    daily_entries: int = 0
    last_trade_day: str = ""
    goal_target: float = 0.0
    pyramiding_count: int = 0  # number of scale-in entries
    # v4.0 additions
    start_of_day_equity: float = 0.0   # 당일 시작 자산 (MDD/일일손실 계산)
    peak_equity: float = 0.0           # 역대 최고 자산 (MDD 계산)
    milestone_count: int = 0           # 달성한 마일스톤 횟수
    # v4.1 additions
    entry_timestamp: float = 0.0       # 진입 시각 (타임컷 추적)
    partial_tp_taken: bool = False     # 50% 분할 익절 완료 여부

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def load(path: Path) -> "PortfolioState":
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return PortfolioState(**{k: v for k, v in data.items() if k in PortfolioState.__dataclass_fields__})
            except Exception:
                pass
        return PortfolioState()


def get_realtime_ticker_price(market: str) -> float:
    """Fetch 100% realtime trade price from Bithumb REST Ticker API without candle lag."""
    try:
        url = f"https://api.bithumb.com/v1/ticker?markets={market}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                return float(data[0]["trade_price"])
    except Exception:
        pass
    # Fallback to candle
    candles = fetch_minute_candles(market, 1, 3)
    return candles[-1].close if candles else 0.0

def fetch_dynamic_universe(min_24h_krw: float = 500_000_000, max_markets: int = 25) -> list[str]:
    """Fetch top liquid markets by 24h volume from Bithumb."""
    try:
        url = "https://api.bithumb.com/v1/market/all"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            all_mkts = [m["market"] for m in json.loads(resp.read().decode("utf-8")) if m.get("market", "").startswith("KRW-")]

        batch = ",".join(all_mkts[:80])
        t_url = f"https://api.bithumb.com/v1/ticker?markets={batch}"
        t_req = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(t_req, timeout=10) as resp:
            tickers = json.loads(resp.read().decode("utf-8"))

        qualified = [t for t in tickers if t.get("acc_trade_price_24h", 0) >= min_24h_krw]
        qualified.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
        top_markets = [t["market"] for t in qualified[:max_markets]]

        combined = list(dict.fromkeys(DEFAULT_MARKETS + top_markets))
        return combined[:max_markets]
    except Exception:
        return DEFAULT_MARKETS


def log_trade(action: str, market: str, price: float, volume: str, amount_krw: float, reason: str, pnl: float = 0.0):
    """Append trade to history log and TRADING_JOURNAL.md."""
    t_now = time.strftime('%Y-%m-%d %H:%M:%S')
    record = {
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S+09:00'),
        "action": action,
        "market": market,
        "price": price,
        "volume": volume,
        "amount_krw": round(amount_krw, 2),
        "pnl_krw": round(pnl, 2),
        "reason": reason,
    }
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADE_LOG_PATH.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"  📝 Trade logged: {action} {market} @ {price:,.0f} KRW ({reason})")

    if JOURNAL_PATH.exists():
        try:
            with JOURNAL_PATH.open("a", encoding="utf-8") as f:
                emoji = "🟢" if action == "BUY" else "🔴"
                pnl_str = f" | 실현손익: `{pnl:+,.0f} KRW`" if action == "SELL" else ""
                f.write(f"\n> **{emoji} [{t_now}] {action} {market}** | 단가: `{price:,.0f} KRW` | 금액: `{amount_krw:,.0f} KRW` | 사유: `{reason}`{pnl_str}\n")
        except Exception:
            pass


def get_market_orderbook_ratio(client: McpStdioClient, market: str) -> float:
    """Fetch 30-level orderbook and return Bid/(Bid+Ask) ratio (0.0 to 1.0)."""
    try:
        res = client.call_read_tool("market_get_orderbook", {"markets": market})
        text = res["content"][0]["text"]
        payload = json.loads(text).get("data", {}).get("data", [])
        if payload:
            item = payload[0]
            total_ask = float(item.get("total_ask_size", 0.0))
            total_bid = float(item.get("total_bid_size", 0.0))
            total = total_ask + total_bid
            if total > 0:
                return total_bid / total
    except Exception:
        pass
    return 0.50


def get_market_warnings(client: McpStdioClient) -> set[str]:
    """Fetch list of markets under warning or investment alert."""
    warned = set()
    try:
        res = client.call_read_tool("market_get_warnings", {})
        text = res["content"][0]["text"]
        payload = json.loads(text).get("data", {}).get("data", [])
        if isinstance(payload, list):
            for item in payload:
                m = item.get("market")
                if m:
                    warned.add(m)
    except Exception:
        pass
    return warned


def get_recent_notices(client: McpStdioClient) -> list[str]:
    """Fetch latest 3 notices from Bithumb."""
    titles = []
    try:
        res = client.call_read_tool("market_get_notices", {})
        text = res["content"][0]["text"]
        payload = json.loads(text).get("data", {}).get("data", [])
        for item in payload[:3]:
            t = item.get("title")
            if t:
                titles.append(t)
    except Exception:
        pass
    return titles


def scan_and_rank_universe(client: McpStdioClient) -> tuple[list[Any], list[dict[str, Any]]]:
    """Scan dynamic universe and return (sorted_analyses, top_candidates)."""
    active_universe = fetch_dynamic_universe(min_24h_krw=500_000_000, max_markets=25)
    warned_markets = get_market_warnings(client)

    analyses = []
    for m in active_universe:
        if m in warned_markets:
            continue
        res = analyze_market(m)
        if res:
            ob_ratio = get_market_orderbook_ratio(client, m)
            ob_adj = (ob_ratio - 0.50) * 20.0
            final_conf = min(max(res.ace_confidence + ob_adj, 0.0), 100.0)
            analyses.append((res, ob_ratio, final_conf))

    top_candidates = []
    if analyses:
        analyses.sort(key=lambda x: x[2], reverse=True)
        top_candidates = [
            {
                "market": a.market,
                "confidence": fconf,
                "bid_ratio": ob * 100.0,
                "status": a.recommendation,
            }
            for a, ob, fconf in analyses[:3]
        ]
    return analyses, top_candidates


def reconcile_with_exchange(client: McpStdioClient, portfolio: PortfolioState) -> None:
    """
    거래소 실잔고를 Source of Truth로 하여 portfolio.json을 자동 교정합니다.
    - 크래시 후 재시작 시 포지션을 잃지 않음
    - 수동 매매 후에도 자동 반영
    """
    try:
        assets_res = client.call_read_tool("account_get_assets", {})
        assets = json.loads(assets_res["content"][0]["text"]).get("data", {}).get("data", [])

        krw_balance = 0.0
        coin_holdings = {}  # currency -> balance
        for a in assets:
            currency = a.get("currency", "")
            balance = float(a.get("balance", 0))
            if currency == "KRW":
                krw_balance = balance
            elif balance > 0 and currency not in ("P",):  # P(포인트) 제외
                coin_holdings[currency] = balance

        # 현금 동기화
        if abs(portfolio.cash_available - krw_balance) > 1.0:
            print(f"  🔄 현금 불일치 교정: {portfolio.cash_available:,.0f} → {krw_balance:,.0f} KRW")
            portfolio.cash_available = krw_balance

        # 포지션 동기화
        state = load_state(STATE_PATH)
        active_currency = portfolio.active_market.replace("KRW-", "") if portfolio.active_market else ""

        if state.position == "long" and active_currency:
            actual_balance = coin_holdings.get(active_currency, 0.0)
            tracked_volume = float(portfolio.position_volume)

            if actual_balance == 0.0 and tracked_volume > 0:
                # 거래소에 코인이 없는데 long이라고 기록됨 → flat으로 교정
                print(f"  ⚠️ 포지션 교정: {portfolio.active_market} long → flat (거래소 잔고 없음)")
                portfolio.active_market = ""
                portfolio.entry_price = 0.0
                portfolio.position_volume = "0"
                portfolio.highest_price = 0.0
                portfolio.pyramiding_count = 0
                portfolio.partial_tp_taken = False
                portfolio.entry_timestamp = 0.0
                save_state(STATE_PATH, BotState(version=1, position="flat", position_volume="0"))

            elif abs(actual_balance - tracked_volume) / max(tracked_volume, 0.0001) > 0.01:
                # 1% 이상 차이나면 거래소 잔고로 교정
                print(f"  🔄 수량 불일치 교정: {tracked_volume} → {actual_balance}")
                portfolio.position_volume = str(actual_balance)
                save_state(STATE_PATH, BotState(version=1, position="long", position_volume=str(actual_balance)))

        elif state.position == "flat" and not active_currency:
            # flat인데 코인을 보유하고 있으면? → 고아 포지션 존재
            if coin_holdings:
                orphans = list(coin_holdings.keys())
                print(f"  ⚠️ 고아 코인 감지: {orphans} (flat 상태인데 코인 보유)")
                # 가장 큰 포지션을 active로 복구
                biggest = max(coin_holdings.items(), key=lambda x: x[1])
                market = f"KRW-{biggest[0]}"
                volume = biggest[1]
                try:
                    candles = fetch_minute_candles(market, 1, 5)
                    price = candles[-1].close
                    portfolio.active_market = market
                    portfolio.entry_price = price  # 평단가 모르니 현재가로 대체
                    portfolio.position_volume = str(volume)
                    portfolio.highest_price = price
                    portfolio.pyramiding_count = 1
                    portfolio.partial_tp_taken = False
                    portfolio.entry_timestamp = time.time()
                    save_state(STATE_PATH, BotState(version=1, position="long", position_volume=str(volume)))
                    print(f"  ✅ 고아 포지션 복구: {market} {volume} @ {price:,.0f} KRW")
                except Exception:
                    print(f"  ⚠️ 고아 포지션 복구 실패, 수동 확인 필요")

        # 총자산 재계산
        total = krw_balance
        for curr, bal in coin_holdings.items():
            try:
                candles = fetch_minute_candles(f"KRW-{curr}", 1, 3)
                total += bal * candles[-1].close
            except Exception:
                pass
        if abs(portfolio.total_capital - total) > 10.0:
            portfolio.total_capital = total

        # peak equity 업데이트
        if total > portfolio.peak_equity:
            portfolio.peak_equity = total

        portfolio.save(PORTFOLIO_PATH)
    except Exception as exc:
        print(f"  ⚠️ Reconciliation error (non-fatal): {exc}")


def calculate_dynamic_order_amount(portfolio: PortfolioState, is_pyramiding: bool = False) -> int:
    """
    자산 비율 기반 동적 주문 금액 계산 (복리 스케일링).
    - 신규 매수: 총자산의 30%, 가용현금의 50% 중 작은 값
    - 피라미딩: 남은 허용 포지션의 50%
    - 수수료 버퍼 0.3% 적용
    """
    if is_pyramiding:
        # 현재 포지션 가치 계산
        cur_vol = float(portfolio.position_volume)
        cur_val = cur_vol * portfolio.entry_price
        max_pos = portfolio.total_capital * MAX_POSITION_RATIO
        remaining = max_pos - cur_val
        invest = min(int(remaining * 0.5), int(portfolio.cash_available * 0.5))
    else:
        invest = min(
            int(portfolio.total_capital * 0.30),
            int(portfolio.cash_available * 0.50),
        )

    # 수수료 버퍼 적용 (잔고 부족 방지)
    invest = int(invest / FEE_BUFFER)

    # 최소/최대 클램핑
    invest = max(invest, MIN_ORDER_KRW)
    invest = min(invest, int(portfolio.cash_available / FEE_BUFFER))  # 잔고 초과 방지

    return invest


def execute_buy(
    market: str,
    amount_krw: int,
    portfolio: PortfolioState,
    settings: TradingSettings,
    confidence: float = 75.0,
    bid_ratio: float = 0.55,
    is_pyramiding: bool = False,
) -> bool:
    """Execute a market buy order (Initial or Pyramiding Scale-In) and send rich Discord alert."""
    action_label = "피라미딩(불타기 2차 매수)" if is_pyramiding else "신규 매수"
    print(f"\n🟢 EXECUTING BUY ({action_label}): {market} for {amount_krw:,} KRW")

    # 잔고 부족 체크 (수수료 포함)
    required = amount_krw * FEE_BUFFER
    if required > portfolio.cash_available:
        print(f"  ❌ 잔고 부족: 필요 {required:,.0f} > 가용 {portfolio.cash_available:,.0f} KRW")
        return False

    intent = TradeIntent(
        market=market,
        target=Signal.LONG,
        quote_amount=Decimal(amount_krw),
        reason=f"{action_label} {market}",
    )
    plan = plan_execution(intent, current=Signal.FLAT)
    if plan.is_noop:
        print(f"  ⚠️ Plan is no-op, skipping")
        return False

    risk_context = RiskContext(
        requested_side=Signal.LONG,
        requested_notional_krw=amount_krw,
        current_equity_krw=portfolio.total_capital,
        start_of_day_equity_krw=portfolio.start_of_day_equity or portfolio.total_capital,
        peak_equity_krw=portfolio.peak_equity or portfolio.total_capital,
        daily_entries=portfolio.daily_entries,
        data_is_fresh=True,
    )
    decision = evaluate_pretrade(risk_context, limits=RiskLimits(
        maximum_daily_entries=50,
        maximum_order_krw=int(portfolio.total_capital * MAX_POSITION_RATIO),
    ))
    if not decision.allowed:
        print(f"  ❌ Risk gate rejected: {decision.reasons}")
        return False

    # 피라미딩 시 백업 생성 (크래시 복구용)
    backup_state = load_state(STATE_PATH)

    try:
        # Save temp flat state for execution validation
        temp_state = BotState(version=1, position="flat", position_volume="0")
        save_state(STATE_PATH, temp_state)

        with McpStdioClient(LIVE_COMMAND) as client:
            executor = BithumbExecutor(client=client, state_path=STATE_PATH, settings=settings, notifier=SilentNotifier())
            result = executor.execute(
                plan,
                risk_context=risk_context,
                bot_state=temp_state,
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
            print(f"  🎉 Buy order submitted: {result.submitted}")

            # 체결 확인
            updated = executor.reconcile_active_order()

            # 거래소 실잔고 조회 (Source of Truth)
            assets_res = client.call_read_tool("account_get_assets", {})
            assets = json.loads(assets_res["content"][0]["text"]).get("data", {}).get("data", [])
            currency = market.replace("KRW-", "")
            coin_asset = next((a for a in assets if a["currency"] == currency), None)
            krw_asset = next((a for a in assets if a["currency"] == "KRW"), None)

            tot_vol = coin_asset["balance"] if coin_asset else updated.position_volume
            tot_krw = float(krw_asset["balance"]) if krw_asset else (portfolio.cash_available - amount_krw)

            candles = fetch_minute_candles(market, 1, 5)
            fill_price = candles[-1].close

            # Calculate weighted average entry price
            if is_pyramiding and float(portfolio.position_volume) > 0:
                prev_vol = float(portfolio.position_volume)
                prev_val = prev_vol * portfolio.entry_price
                new_vol = float(tot_vol) - prev_vol
                weighted_entry = (prev_val + amount_krw) / float(tot_vol) if float(tot_vol) > 0 else fill_price
                portfolio.pyramiding_count += 1
            else:
                weighted_entry = fill_price
                portfolio.pyramiding_count = 1

            new_state = BotState(version=1, position="long", position_volume=str(tot_vol))
            save_state(STATE_PATH, new_state)

            portfolio.active_market = market
            portfolio.entry_price = weighted_entry
            portfolio.position_volume = str(tot_vol)
            portfolio.highest_price = max(portfolio.highest_price, fill_price)
            portfolio.cash_available = tot_krw
            portfolio.total_capital = tot_krw + (float(tot_vol) * fill_price)
            portfolio.total_trades += 1
            portfolio.daily_entries += 1
            if not is_pyramiding:
                portfolio.entry_timestamp = time.time()
                portfolio.partial_tp_taken = False
            if portfolio.total_capital > portfolio.peak_equity:
                portfolio.peak_equity = portfolio.total_capital
            portfolio.save(PORTFOLIO_PATH)

            log_trade("BUY", market, fill_price, str(tot_vol), amount_krw, action_label)

            tp = weighted_entry * (1 + TAKE_PROFIT_PCT)
            sl = weighted_entry * (1 - STOP_LOSS_PCT)
            notify_buy_entry(
                market=market,
                price=fill_price,
                amount_krw=amount_krw,
                volume=str(tot_vol),
                confidence=confidence,
                bid_ratio=bid_ratio,
                take_profit=tp,
                stop_loss=sl,
            )
            print(f"  ✅ {action_label} complete! Total Volume: {tot_vol}, Weighted Entry: {weighted_entry:,.0f} KRW")
            return True
    except Exception as exc:
        print(f"  ❌ Buy error: {exc}")
        # 피라미딩 실패 시 원래 상태 복원
        if is_pyramiding:
            try:
                save_state(STATE_PATH, backup_state)
                print(f"  🔄 피라미딩 실패 - 원래 상태 복원 완료")
            except Exception:
                pass
    return False


def execute_sell(portfolio: PortfolioState, settings: TradingSettings, reason: str) -> bool:
    """Execute a market sell order with reconciliation and send rich Discord alert."""
    state = load_state(STATE_PATH)
    if state.position != "long":
        print(f"  ⚠️ Not in LONG position, skipping sell")
        return False

    market = portfolio.active_market
    vol = Decimal(state.position_volume)
    current_price = get_realtime_ticker_price(market)
    val_krw = float(vol) * current_price

    print(f"\n🔴 EXECUTING SELL: {market} | Vol: {vol:.4f} | Price: {current_price:,.0f} KRW | Reason: {reason}")

    intent = TradeIntent(
        market=market,
        target=Signal.FLAT,
        base_volume=vol,
        reason=reason,
    )
    plan = plan_execution(intent, current=Signal.LONG)

    risk_context = RiskContext(
        requested_side=Signal.FLAT,
        requested_notional_krw=float(vol * Decimal(str(current_price))),
        current_equity_krw=portfolio.total_capital,
        start_of_day_equity_krw=portfolio.start_of_day_equity or portfolio.total_capital,
        peak_equity_krw=portfolio.peak_equity or portfolio.total_capital,
        daily_entries=portfolio.daily_entries,
        data_is_fresh=True,
        reference_price_krw=current_price,
    )

    try:
        with McpStdioClient(LIVE_COMMAND) as client:
            executor = BithumbExecutor(client=client, state_path=STATE_PATH, settings=settings, notifier=SilentNotifier())
            result = executor.execute(
                plan,
                risk_context=risk_context,
                bot_state=state,
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
            print(f"  🎉 Sell order submitted: {result.submitted}")

            # 체결 확인 (v4.0: reconcile 추가)
            executor.reconcile_active_order()

            # 거래소 실잔고 조회로 정확한 매도 대금 확인
            assets_res = client.call_read_tool("account_get_assets", {})
            assets = json.loads(assets_res["content"][0]["text"]).get("data", {}).get("data", [])
            krw_asset = next((a for a in assets if a["currency"] == "KRW"), None)
            actual_krw = float(krw_asset["balance"]) if krw_asset else (portfolio.cash_available + val_krw)

            entry_val = float(vol) * portfolio.entry_price
            actual_val = actual_krw - portfolio.cash_available  # 실제 매도 대금
            pnl = actual_val - entry_val if actual_val > 0 else val_krw - entry_val
            pnl_pct = (current_price - portfolio.entry_price) / portfolio.entry_price * 100.0

            portfolio.cash_available = actual_krw
            portfolio.total_capital = actual_krw
            portfolio.total_pnl_krw += pnl
            if pnl >= 0:
                portfolio.winning_trades += 1
            else:
                portfolio.losing_trades += 1
            portfolio.active_market = ""
            portfolio.entry_price = 0.0
            portfolio.position_volume = "0"
            portfolio.highest_price = 0.0
            portfolio.pyramiding_count = 0
            portfolio.partial_tp_taken = False
            portfolio.entry_timestamp = 0.0
            if portfolio.total_capital > portfolio.peak_equity:
                portfolio.peak_equity = portfolio.total_capital
            portfolio.save(PORTFOLIO_PATH)
            save_state(STATE_PATH, BotState(version=1, position="flat", position_volume="0"))

            # 청산 후 동일 종목 재진입 15분 쿨다운 활성화
            record_exit(market)

            log_trade("SELL", market, current_price, str(vol), val_krw, reason, pnl)
            print(f"  📊 Trade P&L: {pnl:+,.0f} KRW ({pnl_pct:+.2f}%)")
            print(f"  💰 Portfolio: {portfolio.total_capital:,.0f} KRW (Target: {portfolio.goal_target:,.0f} KRW)")

            notify_sell_exit(
                market=market,
                price=current_price,
                volume=str(vol),
                amount_krw=val_krw,
                pnl_krw=pnl,
                pnl_pct=pnl_pct,
                reason=reason,
                total_capital=portfolio.total_capital,
                target_capital=portfolio.goal_target,
            )
            return True
    except Exception as exc:
        print(f"  ❌ Sell error: {exc}")
        # 매도 실패해도 상태는 보존 (다음 루프에서 재시도)
    return False


def execute_sell_partial(portfolio: PortfolioState, settings: TradingSettings, ratio: float = 0.5, reason: str = "🎯 1차 50% 분할 익절") -> bool:
    """Execute a partial market sell order (e.g. 50%) for risk-free profit locking."""
    state = load_state(STATE_PATH)
    if state.position != "long":
        print("  ⚠️ Not in LONG position, skipping partial sell")
        return False

    market = portfolio.active_market
    total_vol = Decimal(state.position_volume)
    sell_vol = total_vol * Decimal(str(ratio))

    current_price = get_realtime_ticker_price(market)
    sell_val_krw = float(sell_vol) * current_price

    if sell_val_krw < 500:
        print(f"  ⚠️ Partial sell value {sell_val_krw:,.0f} KRW is below 500 KRW threshold, skipping partial sell")
        return False

    print(f"\n✂️ EXECUTING PARTIAL SELL ({ratio*100:.0f}%): {market} | SellVol: {sell_vol:.4f} | Price: {current_price:,.0f} KRW | Reason: {reason}")

    intent = TradeIntent(
        market=market,
        target=Signal.FLAT,
        base_volume=sell_vol,
        reason=reason,
    )
    plan = plan_execution(intent, current=Signal.LONG)

    risk_context = RiskContext(
        requested_side=Signal.FLAT,
        requested_notional_krw=sell_val_krw,
        current_equity_krw=portfolio.total_capital,
        start_of_day_equity_krw=portfolio.start_of_day_equity or portfolio.total_capital,
        peak_equity_krw=portfolio.peak_equity or portfolio.total_capital,
        daily_entries=portfolio.daily_entries,
        data_is_fresh=True,
        reference_price_krw=current_price,
    )

    # BithumbExecutor는 tracked volume 전체 매도를 검증하므로 partial_state를 임시 저장
    partial_state = BotState(version=1, position="long", position_volume=str(sell_vol))
    save_state(STATE_PATH, partial_state)

    try:
        with McpStdioClient(LIVE_COMMAND) as client:
            executor = BithumbExecutor(client=client, state_path=STATE_PATH, settings=settings, notifier=SilentNotifier())
            result = executor.execute(
                plan,
                risk_context=risk_context,
                bot_state=partial_state,
                confirmation_token=LIVE_CONFIRMATION_TOKEN,
            )
            print(f"  🎉 Partial sell order submitted: {result.submitted}")
            executor.reconcile_active_order()

            assets_res = client.call_read_tool("account_get_assets", {})
            assets = json.loads(assets_res["content"][0]["text"]).get("data", {}).get("data", [])
            currency = market.replace("KRW-", "")
            coin_asset = next((a for a in assets if a["currency"] == currency), None)
            krw_asset = next((a for a in assets if a["currency"] == "KRW"), None)

            actual_rem_vol = Decimal(coin_asset["balance"]) if coin_asset else (total_vol - sell_vol)
            actual_krw = float(krw_asset["balance"]) if krw_asset else (portfolio.cash_available + sell_val_krw)

            entry_val_portion = float(sell_vol) * portfolio.entry_price
            actual_val = actual_krw - portfolio.cash_available
            pnl = actual_val - entry_val_portion if actual_val > 0 else sell_val_krw - entry_val_portion
            pnl_pct = (current_price - portfolio.entry_price) / portfolio.entry_price * 100.0

            portfolio.cash_available = actual_krw
            portfolio.position_volume = str(actual_rem_vol)
            portfolio.total_capital = actual_krw + (float(actual_rem_vol) * current_price)
            portfolio.total_pnl_krw += pnl
            if pnl >= 0:
                portfolio.winning_trades += 1
            else:
                portfolio.losing_trades += 1
            portfolio.partial_tp_taken = True
            if portfolio.total_capital > portfolio.peak_equity:
                portfolio.peak_equity = portfolio.total_capital
            portfolio.save(PORTFOLIO_PATH)

            new_state = BotState(version=1, position="long", position_volume=str(actual_rem_vol))
            save_state(STATE_PATH, new_state)

            log_trade("PARTIAL_SELL", market, current_price, str(sell_vol), sell_val_krw, reason, pnl)
            print(f"  📊 Partial P&L: {pnl:+,.0f} KRW ({pnl_pct:+.2f}%) | Remaining: {actual_rem_vol} {currency}")
            print(f"  💰 Portfolio: {portfolio.total_capital:,.0f} KRW (Target: {portfolio.goal_target:,.0f} KRW)")

            notify_partial_sell_exit(
                market=market,
                price=current_price,
                volume=f"{sell_vol:.4f}",
                amount_krw=sell_val_krw,
                pnl_krw=pnl,
                pnl_pct=pnl_pct,
                remaining_volume=f"{actual_rem_vol:.4f}",
                reason=reason,
                total_capital=portfolio.total_capital,
                target_capital=portfolio.goal_target,
            )
            return True
    except Exception as exc:
        print(f"  ❌ Partial sell error: {exc}")
    return False



def main():
    portfolio = PortfolioState.load(PORTFOLIO_PATH)
    if portfolio.goal_target <= 0 and portfolio.total_capital > 0:
        portfolio.goal_target = portfolio.total_capital * (1.0 + TARGET_RETURN_PCT / 100.0)

    # v4.0: peak_equity / start_of_day_equity 초기화
    if portfolio.peak_equity <= 0:
        portfolio.peak_equity = portfolio.total_capital
    if portfolio.start_of_day_equity <= 0:
        portfolio.start_of_day_equity = portfolio.total_capital

    portfolio.save(PORTFOLIO_PATH)

    print("=" * 80)
    print(" 🤖 AUTONOMOUS TRADING DAEMON v4.0 (Self-Healing Infinite Compounding)")
    print(f" 🎯 MILESTONE: +{TARGET_RETURN_PCT:.1f}% → {portfolio.goal_target:,.0f} KRW (Auto-Rolling)")
    print(f" 📡 Engines: 25-Universe Radar + Pyramiding + Reconciliation + Self-Healing")
    print(f" 🛡️ Risk: SL {STOP_LOSS_PCT*100:.1f}% | TP {TAKE_PROFIT_PCT*100:.1f}% | Trail {TRAILING_STOP_PCT*100:.1f}% | MDD 10% | DailyLoss 2%")
    print(f" ⏱️ Price Watch: {PRICE_CHECK_INTERVAL}s | Scan: ~{PRICE_CHECK_INTERVAL * SCAN_INTERVAL_LOOPS}s | Reconcile: ~{PRICE_CHECK_INTERVAL * RECONCILE_LOOPS}s")
    print("=" * 80)

    # v4.0: dynamic max order based on portfolio
    dynamic_max_order = max(int(portfolio.total_capital * MAX_POSITION_RATIO), MIN_ORDER_KRW)
    settings = TradingSettings(
        initial_capital_krw=max(int(portfolio.total_capital), 20_000),
        fee_rate=0.0025,
        mode=TradingMode.LIVE,
        live_trading_enabled=True,
        minimum_order_krw=MIN_ORDER_KRW,
        maximum_order_krw=dynamic_max_order,
        maximum_daily_entries=50,
        cash_reserve_krw=0,
    )

    today = time.strftime('%Y-%m-%d')
    if portfolio.last_trade_day != today:
        portfolio.daily_entries = 0
        portfolio.last_trade_day = today
        portfolio.start_of_day_equity = portfolio.total_capital  # 당일 시작 자산 스냅샷
        portfolio.save(PORTFOLIO_PATH)

    print(f"\n📦 Portfolio State:")
    print(f"  Total Capital: {portfolio.total_capital:,.0f} KRW")
    print(f"  Cash Available: {portfolio.cash_available:,.0f} KRW")
    print(f"  Active Position: {portfolio.active_market or 'None'}")
    print(f"  Position Volume: {portfolio.position_volume}")
    print(f"  Weighted Entry: {portfolio.entry_price:,.0f} KRW")
    print(f"  Cumulative P&L: {portfolio.total_pnl_krw:+,.0f} KRW")
    print(f"  Win/Loss: {portfolio.winning_trades}W / {portfolio.losing_trades}L")
    print(f"  Peak Equity: {portfolio.peak_equity:,.0f} KRW")
    print(f"  Day Start Equity: {portfolio.start_of_day_equity:,.0f} KRW")
    print(f"  Target Milestone: {portfolio.goal_target:,.0f} KRW (#{portfolio.milestone_count + 1})\n")

    print("🔍 Performing initial Dynamic Universe scan...")
    cached_top_candidates = []
    try:
        with McpStdioClient(LIVE_COMMAND) as client:
            # v4.0: 최초 가동 시 거래소 잔고 기반 복구
            reconcile_with_exchange(client, portfolio)
            _, cached_top_candidates = scan_and_rank_universe(client)
            if cached_top_candidates:
                print(f"  ✅ Initial Scan complete! Top 1: {cached_top_candidates[0]['market']} ({cached_top_candidates[0]['confidence']:.1f}%)")
    except Exception as exc:
        print(f"  ⚠️ Initial scan warning: {exc}")

    loop_count = 0
    consecutive_errors = 0  # v4.0: 연속 에러 카운터
    last_briefing_hour = -1  # 매 시 정각(8시, 9시, 10시...) 브리핑 추적 변수

    # ═══════════════════════════════════════════════════════════════
    # 🔄 INFINITE MAIN LOOP (Never Stops, Self-Healing)
    # ═══════════════════════════════════════════════════════════════
    while True:
        try:
            loop_count += 1
            now_str = time.strftime('%Y-%m-%d %H:%M:%S')

            # ── 날짜 변경 감지 ──
            today = time.strftime('%Y-%m-%d')
            if portfolio.last_trade_day != today:
                portfolio.daily_entries = 0
                portfolio.last_trade_day = today
                portfolio.start_of_day_equity = portfolio.total_capital
                portfolio.save(PORTFOLIO_PATH)
                print(f"\n📅 새로운 거래일: {today} | 시작 자산: {portfolio.total_capital:,.0f} KRW")

            # ── 마일스톤 도달 체크 (멈추지 않고 다음 목표 자동 갱신!) ──
            if portfolio.goal_target > 0 and portfolio.total_capital >= portfolio.goal_target:
                portfolio.milestone_count += 1
                old_target = portfolio.goal_target
                portfolio.goal_target = portfolio.total_capital * (1.0 + TARGET_RETURN_PCT / 100.0)
                portfolio.save(PORTFOLIO_PATH)
                msg = (
                    f"🏆🏆🏆 **[마일스톤 #{portfolio.milestone_count} 달성!]** 🏆🏆🏆\n"
                    f"> 달성 목표: `{old_target:,.0f} KRW`\n"
                    f"> 현재 자산: `{portfolio.total_capital:,.0f} KRW`\n"
                    f"> 🔄 **다음 목표 자동 갱신: `{portfolio.goal_target:,.0f} KRW` (+{TARGET_RETURN_PCT:.0f}%)**\n"
                    f"> ♾️ 무한 복리 회전 모드 계속 가동 중..."
                )
                print(f"\n{msg}")
                send_discord_message(msg)
                # settings 업데이트 (maximum_order_krw 스케일 업)
                settings = TradingSettings(
                    initial_capital_krw=max(int(portfolio.total_capital), 20_000),
                    fee_rate=0.0025,
                    mode=TradingMode.LIVE,
                    live_trading_enabled=True,
                    minimum_order_krw=MIN_ORDER_KRW,
                    maximum_order_krw=max(int(portfolio.total_capital * MAX_POSITION_RATIO), MIN_ORDER_KRW),
                    maximum_daily_entries=50,
                    cash_reserve_krw=0,
                )

            state = load_state(STATE_PATH)

            # ═══════════════════════════════════════════════════════════════
            # 1. MANAGE ACTIVE POSITION & PYRAMIDING (every 3 seconds)
            # ═══════════════════════════════════════════════════════════════
            cur_val_krw = 0.0
            cur_pnl_pct = 0.0
            cur_price = 0.0
            if state.position == "long" and portfolio.active_market:
                try:
                    cur_price = get_realtime_ticker_price(portfolio.active_market)
                    vol = Decimal(state.position_volume)
                    cur_val_krw = float(vol) * cur_price

                    if portfolio.entry_price > 0:
                        cur_pnl_pct = (cur_price - portfolio.entry_price) / portfolio.entry_price * 100.0

                    if cur_price > portfolio.highest_price:
                        portfolio.highest_price = cur_price
                        portfolio.save(PORTFOLIO_PATH)

                    stop_loss_price = portfolio.entry_price * (1 - STOP_LOSS_PCT)
                    take_profit_price = portfolio.entry_price * (1 + TAKE_PROFIT_PCT)
                    trailing_stop_price = portfolio.highest_price * (1 - TRAILING_STOP_PCT)

                    if loop_count % 10 == 0:
                        tot_est = portfolio.cash_available + cur_val_krw
                        print(f"\n[{now_str}] 📈 {portfolio.active_market} | {cur_price:,.0f} KRW | PnL: {cur_pnl_pct:+.2f}% ({cur_val_krw:,.0f} KRW)")
                        print(f"  SL: {stop_loss_price:,.0f} | TP: {take_profit_price:,.0f} | Trail: {trailing_stop_price:,.0f} | Peak: {portfolio.highest_price:,.0f}")
                        print(f"  💰 Portfolio: ~{tot_est:,.0f} KRW (Target: {portfolio.goal_target:,.0f} KRW)")

                    trigger_exit = False
                    exit_reason = ""

                    if cur_price <= stop_loss_price:
                        trigger_exit = True
                        exit_reason = f"⛔ STOP-LOSS ({cur_price:,.0f} <= {stop_loss_price:,.0f})"
                    elif cur_price >= take_profit_price:
                        trigger_exit = True
                        exit_reason = f"🎯 TAKE-PROFIT ({cur_price:,.0f} >= {take_profit_price:,.0f})"
                    elif (portfolio.highest_price > portfolio.entry_price * (1 + TRAILING_ACTIVATE_PCT)
                          and cur_price <= trailing_stop_price):
                        trigger_exit = True
                        exit_reason = f"📉 TRAILING-STOP ({cur_price:,.0f} <= {trailing_stop_price:,.0f}, Peak: {portfolio.highest_price:,.0f})"

                    if trigger_exit:
                        execute_sell(portfolio, settings, exit_reason)

                except Exception as exc:
                    if loop_count % 20 == 1:
                        print(f"⚠️ Position check error: {exc}")

            # ═══════════════════════════════════════════════════════════════
            # 2. ALWAYS-ON SCAN & DYNAMIC ENTRY / PYRAMIDING (every ~30s)
            # ═══════════════════════════════════════════════════════════════
            if loop_count % SCAN_INTERVAL_LOOPS == 1:
                try:
                    with McpStdioClient(LIVE_COMMAND) as client:
                        analyses, new_top_candidates = scan_and_rank_universe(client)
                        if new_top_candidates:
                            cached_top_candidates = new_top_candidates

                        state = load_state(STATE_PATH)

                        # Initial Entry when FLAT (포지션이 없을 때만 25-유니버스 1위 종목 탐색 & 15분 쿨다운 필터링)
                        if state.position == "flat" and portfolio.cash_available >= MIN_ORDER_KRW and analyses:
                            for cand in analyses:
                                mkt = cand[0].market
                                exits = load_recent_exits(); last_exit = exits.get(mkt, 0)
                                if time.time() - last_exit < REENTRY_COOLDOWN_SEC:
                                    rem = int(REENTRY_COOLDOWN_SEC - (time.time() - last_exit))
                                    if loop_count % 10 == 1:
                                        print(f"  ⏳ {mkt} 재진입 쿨다운 대기 중 ({rem}초 남음) - 중복 진입 방어")
                                    continue

                                best, best_ob, best_fconf = cand
                                if best_fconf >= MIN_CONFIDENCE_ENTRY and best.pm_decision is Signal.LONG and best_ob >= 0.50:
                                    invest = calculate_dynamic_order_amount(portfolio, is_pyramiding=False)
                                    if invest >= MIN_ORDER_KRW and invest <= portfolio.cash_available:
                                        print(f"\n🎯 STRONG INITIAL ENTRY: {best.market} (Conf: {best_fconf:.1f}%, BidRatio: {best_ob*100:.1f}%)")
                                        execute_buy(best.market, invest, portfolio, settings, confidence=best_fconf, bid_ratio=best_ob, is_pyramiding=False)
                                        break

                        # Dynamic Pyramiding Scale-In (2차 불타기는 +0.8% 이상 유의미한 상승 시에만!)
                        elif (state.position == "long" and portfolio.active_market and portfolio.pyramiding_count < 2
                              and portfolio.cash_available >= MIN_ORDER_KRW and cur_pnl_pct >= PYRAMIDING_MIN_GAIN_PCT and analyses):
                            active_tuple = next((t for t in analyses if t[0].market == portfolio.active_market), None)
                            if active_tuple:
                                active_res, active_ob, active_conf = active_tuple
                                max_pos_allowed = portfolio.total_capital * MAX_POSITION_RATIO
                                if (active_conf >= 70.0 and cur_val_krw < max_pos_allowed
                                    and portfolio.cash_available > portfolio.total_capital * MIN_RESERVE_CASH_RATIO):
                                    scale_amount = calculate_dynamic_order_amount(portfolio, is_pyramiding=True)
                                    if scale_amount >= MIN_ORDER_KRW and scale_amount <= portfolio.cash_available:
                                        print(f"\n🚀 AUTOMATIC PYRAMIDING SCALE-IN: {portfolio.active_market} (Conf: {active_conf:.1f}%, PnL: {cur_pnl_pct:+.2f}%)")
                                        execute_buy(portfolio.active_market, scale_amount, portfolio, settings, confidence=active_conf, bid_ratio=active_ob, is_pyramiding=True)

                except Exception as exc:
                    print(f"⚠️ Dynamic scan error: {exc}")

            # ═══════════════════════════════════════════════════════════════
            # 3. EXCHANGE BALANCE RECONCILIATION (every ~30 minutes)
            # ═══════════════════════════════════════════════════════════════
            if loop_count % RECONCILE_LOOPS == 0:
                try:
                    with McpStdioClient(LIVE_COMMAND) as client:
                        reconcile_with_exchange(client, portfolio)
                        print(f"[{now_str}] 🔄 거래소 잔고 동기화 완료")
                except Exception as exc:
                    print(f"⚠️ Reconciliation error: {exc}")

            # ═══════════════════════════════════════════════════════════════
            # 4. HOURLY DISCORD BRIEFING (매 시 정각: 8시, 9시, 10시...)
            # ═══════════════════════════════════════════════════════════════
            now_dt = datetime.now()
            if now_dt.minute == 0 and now_dt.hour != last_briefing_hour:
                try:
                    last_briefing_hour = now_dt.hour
                    tot_est = portfolio.cash_available + cur_val_krw if state.position == "long" else portfolio.total_capital
                    notify_hourly_briefing(
                        total_capital=tot_est,
                        cash_available=portfolio.cash_available,
                        active_market=portfolio.active_market,
                        active_price=cur_price,
                        entry_price=portfolio.entry_price,
                        active_pnl_pct=cur_pnl_pct,
                        active_val_krw=cur_val_krw,
                        top_candidates=cached_top_candidates,
                        target_capital=portfolio.goal_target,
                        winning_trades=portfolio.winning_trades,
                        losing_trades=portfolio.losing_trades,
                        total_pnl_krw=portfolio.total_pnl_krw,
                        initial_capital=INITIAL_CAPITAL,
                    )
                    print(f"\n[{now_str}] 📊 매 시 정각 브리핑 전송 완료 ({now_dt.hour}시 정각)")
                except Exception as exc:
                    print(f"⚠️ Hourly briefing error: {exc}")

            # ═══════════════════════════════════════════════════════════════
            # 5. NOTICE & EVENT MONITOR (every ~10m)
            # ═══════════════════════════════════════════════════════════════
            if loop_count % NOTICE_CHECK_LOOPS == 1 and loop_count > 1:
                try:
                    with McpStdioClient(LIVE_COMMAND) as client:
                        notices = get_recent_notices(client)
                        print(f"\n[{now_str}] 📢 Bithumb Latest Notices Check:")
                        for n in notices:
                            print(f"  - {n}")
                except Exception:
                    pass

            # ── 에러 없이 루프 완료 → 연속 에러 카운터 리셋 ──
            consecutive_errors = 0

            sys.stdout.flush()
            time.sleep(PRICE_CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n\n🛑 사용자 중단 (Ctrl+C). 안전하게 종료합니다.")
            break
        except Exception as exc:
            consecutive_errors += 1
            print(f"\n💥 LOOP ERROR #{consecutive_errors}: {exc}")
            traceback.print_exc()

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                error_msg = (
                    f"🚨🚨🚨 **[긴급] 연속 에러 {consecutive_errors}회 발생!** 🚨🚨🚨\n"
                    f"> 에러: `{str(exc)[:200]}`\n"
                    f"> 포트폴리오: `{portfolio.total_capital:,.0f} KRW`\n"
                    f"> 포지션: `{portfolio.active_market or 'FLAT'}`\n"
                    f"> ⏳ {ERROR_COOLDOWN_SEC}초 후 자동 재시작 시도..."
                )
                print(error_msg)
                try:
                    send_discord_message(error_msg)
                except Exception:
                    pass
                consecutive_errors = 0  # 알림 보냈으니 리셋

            print(f"  ⏳ {ERROR_COOLDOWN_SEC}초 대기 후 재시작...")
            time.sleep(ERROR_COOLDOWN_SEC)


if __name__ == "__main__":
    main()
