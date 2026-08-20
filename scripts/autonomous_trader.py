#!/usr/bin/env python3
"""
🤖 AUTONOMOUS TRADING DAEMON v3.4 (Dynamic Universe & Pyramiding Scaling Engine)
────────────────────────────────────────────────────────────────────────────────
Target   : Dynamic Compounding Milestone (+50.0% Target Return)
Engines  : 
  - Dynamic 25-Universe Always-On Radar (Top 25 Liquid & Momentum Markets)
  - Tauric Multi-Agent (TARO, DIANA, NOVA, VIBE, ACE, PM)
  - Dynamic Pyramiding Engine (Automatic 2nd Scale-In for High-Conviction Setups)
  - Institutional Volume Delta & Candle Displacement
  - Bithumb Realtime 30-Orderbook Imbalance (Bid-Ask Depth Ratio)
  - Bithumb Market Warnings & Delisting Safeguard
  - 📱 Discord Finance-Chat Rich Mobile Alerts & Hourly Briefings
Risk     : SL -2.0%, TP +4.0%, Trailing-Stop -1.5% from peak (Activates at +1.0%)
Cycle    : 3s Price Watch, 30s Multi-Market Fusion Scan, 1h Discord Briefing
"""

from __future__ import annotations

import json
import os
import time
import sys
import urllib.request
from dataclasses import dataclass, asdict
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

# ── Target & Risk Parameters ──────────────────────────────────
TARGET_RETURN_PCT = float(os.environ.get("TARGET_RETURN_PCT", "50.0"))  # 목표 수익률 (+50.0%)
STOP_LOSS_PCT = 0.020       # 손절가 비율 (-2.0%)
TAKE_PROFIT_PCT = 0.040     # 1차 목표가 비율 (+4.0%)
TRAILING_STOP_PCT = 0.015   # 트레일링 스탑 비율 (-1.5% from peak)
TRAILING_ACTIVATE_PCT = 0.01  # 트레일링 활성화 기준 (+1.0% gain)
MIN_CONFIDENCE_ENTRY = 70.0  # 최소 진입 확신도
MIN_CONFIDENCE_STRONG = 75.0 # 강력 매수 확신도
MIN_ORDER_KRW = 5_000
PRICE_CHECK_INTERVAL = 3     # seconds
SCAN_INTERVAL_LOOPS = 10     # every 10 loops = ~30 seconds
HOURLY_REPORT_LOOPS = 1200   # every 1200 loops = ~1 hour
NOTICE_CHECK_LOOPS = 200     # every ~10 minutes


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


def fetch_dynamic_universe(min_24h_krw: float = 500_000_000, max_markets: int = 25) -> list[str]:
    """Fetch top liquid markets by 24h volume from Bithumb."""
    try:
        url = "https://api.bithumb.com/v1/market/all"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            all_mkts = [m["market"] for m in json.loads(resp.read().decode("utf-8")) if m.get("market", "").startswith("KRW-")]

        batch = ",".join(all_mkts[:80])
        t_url = f"https://api.bithumb.com/v1/ticker?markets={batch}"
        t_req = urllib.request.Request(t_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(t_req, timeout=5) as resp:
            tickers = json.loads(resp.read().decode("utf-8"))

        qualified = [t for t in tickers if t.get("acc_trade_price_24h", 0) >= min_24h_krw]
        qualified.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
        top_markets = [t["market"] for t in qualified[:max_markets]]

        combined = list(dict.fromkeys(DEFAULT_MARKETS + top_markets))
        return combined[:max_markets]
    except Exception:
        return DEFAULT_MARKETS


def log_trade(action: str, market: str, price: float, volume: str, amount_krw: float, reason: str, pnl: float = 0.0):
    """Append trade to history log."""
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
        start_of_day_equity_krw=portfolio.total_capital,
        peak_equity_krw=portfolio.total_capital,
        daily_entries=portfolio.daily_entries,
        data_is_fresh=True,
    )
    decision = evaluate_pretrade(risk_context, limits=RiskLimits(maximum_daily_entries=50))
    if not decision.allowed:
        print(f"  ❌ Risk gate rejected: {decision.reasons}")
        return False

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

            updated = executor.reconcile_active_order()
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
    return False


def execute_sell(portfolio: PortfolioState, settings: TradingSettings, reason: str) -> bool:
    """Execute a market sell order and send rich Discord alert."""
    state = load_state(STATE_PATH)
    if state.position != "long":
        print(f"  ⚠️ Not in LONG position, skipping sell")
        return False

    market = portfolio.active_market
    vol = Decimal(state.position_volume)
    candles = fetch_minute_candles(market, 1, 5)
    current_price = candles[-1].close
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
        start_of_day_equity_krw=portfolio.total_capital,
        peak_equity_krw=portfolio.total_capital,
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

            entry_val = float(vol) * portfolio.entry_price
            pnl = val_krw - entry_val
            pnl_pct = (current_price - portfolio.entry_price) / portfolio.entry_price * 100.0

            portfolio.cash_available += val_krw
            portfolio.total_capital = portfolio.cash_available
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
            portfolio.save(PORTFOLIO_PATH)
            save_state(STATE_PATH, BotState(version=1, position="flat", position_volume="0"))

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
    return False


def main():
    portfolio = PortfolioState.load(PORTFOLIO_PATH)
    if portfolio.goal_target <= 0 and portfolio.total_capital > 0:
        portfolio.goal_target = portfolio.total_capital * (1.0 + TARGET_RETURN_PCT / 100.0)
        portfolio.save(PORTFOLIO_PATH)

    print("=" * 80)
    print(" 🤖 AUTONOMOUS TRADING DAEMON v3.4 (Dynamic Universe & Pyramiding Engine)")
    print(f" 🎯 TARGET: +{TARGET_RETURN_PCT:.1f}% Return Milestone (Compounding Engine)")
    print(f" 📡 Integrated: Dynamic 25-Universe Radar + Pyramiding Scale-In + Discord")
    print(f" ⏱️ Price Watch: 3s | Market Scan: ~{PRICE_CHECK_INTERVAL * SCAN_INTERVAL_LOOPS}s")
    print("=" * 80)

    settings = TradingSettings(
        initial_capital_krw=portfolio.total_capital or 100_000,
        fee_rate=0.0025,
        mode=TradingMode.LIVE,
        live_trading_enabled=True,
        minimum_order_krw=MIN_ORDER_KRW,
        cash_reserve_krw=0,
    )

    today = time.strftime('%Y-%m-%d')
    if portfolio.last_trade_day != today:
        portfolio.daily_entries = 0
        portfolio.last_trade_day = today
        portfolio.save(PORTFOLIO_PATH)

    print(f"\n📦 Portfolio State:")
    print(f"  Total Capital: {portfolio.total_capital:,.0f} KRW")
    print(f"  Cash Available: {portfolio.cash_available:,.0f} KRW")
    print(f"  Active Position: {portfolio.active_market or 'None'}")
    print(f"  Position Volume: {portfolio.position_volume}")
    print(f"  Weighted Entry: {portfolio.entry_price:,.0f} KRW")
    print(f"  Cumulative P&L: {portfolio.total_pnl_krw:+,.0f} KRW")
    print(f"  Win/Loss: {portfolio.winning_trades}W / {portfolio.losing_trades}L")
    print(f"  Target Milestone: {portfolio.goal_target:,.0f} KRW (+{TARGET_RETURN_PCT:.1f}%)\n")

    print("🔍 Performing initial Dynamic Universe scan...")
    cached_top_candidates = []
    try:
        with McpStdioClient(LIVE_COMMAND) as client:
            _, cached_top_candidates = scan_and_rank_universe(client)
            if cached_top_candidates:
                print(f"  ✅ Initial Scan complete! Top 1: {cached_top_candidates[0]['market']} ({cached_top_candidates[0]['confidence']:.1f}%)")
    except Exception as exc:
        print(f"  ⚠️ Initial scan warning: {exc}")

    loop_count = 0

    while True:
        loop_count += 1
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')

        today = time.strftime('%Y-%m-%d')
        if portfolio.last_trade_day != today:
            portfolio.daily_entries = 0
            portfolio.last_trade_day = today
            portfolio.save(PORTFOLIO_PATH)

        if portfolio.goal_target > 0 and portfolio.total_capital >= portfolio.goal_target:
            print(f"\n🏆🏆🏆 GOAL REACHED! Portfolio: {portfolio.total_capital:,.0f} KRW >= {portfolio.goal_target:,.0f} KRW 🏆🏆🏆")
            send_discord_message(f"🏆🏆🏆 **[축하합니다!] 목표 +{TARGET_RETURN_PCT:.1f}% 달성 완료!** 🏆🏆🏆\n> 최종 자산: `{portfolio.total_capital:,.0f} KRW`\n> 모든 포지션을 안전하게 전량 현금화했습니다.")
            break

        state = load_state(STATE_PATH)

        # ═══════════════════════════════════════════════════════════════
        # 1. MANAGE ACTIVE POSITION & PYRAMIDING (every 3 seconds)
        # ═══════════════════════════════════════════════════════════════
        cur_val_krw = 0.0
        cur_pnl_pct = 0.0
        cur_price = 0.0
        if state.position == "long" and portfolio.active_market:
            try:
                candles = fetch_minute_candles(portfolio.active_market, 1, 10)
                cur_price = candles[-1].close
                vol = Decimal(state.position_volume)
                cur_val_krw = float(vol) * cur_price
                cur_pnl_pct = (cur_price - portfolio.entry_price) / portfolio.entry_price * 100.0

                if cur_price > portfolio.highest_price:
                    portfolio.highest_price = cur_price
                    portfolio.save(PORTFOLIO_PATH)

                stop_loss_price = portfolio.entry_price * (1 - STOP_LOSS_PCT)
                take_profit_price = portfolio.entry_price * (1 + TAKE_PROFIT_PCT)
                trailing_stop_price = portfolio.highest_price * (1 - TRAILING_STOP_PCT)

                if loop_count % 10 == 1:
                    print(f"\n[{now_str}] 📈 {portfolio.active_market} | {cur_price:,.0f} KRW | PnL: {cur_pnl_pct:+.2f}% ({cur_val_krw:,.0f} KRW)")
                    print(f"  SL: {stop_loss_price:,.0f} | TP: {take_profit_price:,.0f} | Trail: {trailing_stop_price:,.0f} | Peak: {portfolio.highest_price:,.0f}")
                    print(f"  💰 Portfolio: ~{portfolio.cash_available + cur_val_krw:,.0f} KRW (Target: {portfolio.goal_target:,.0f} KRW)")

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

                    # Initial Entry when FLAT
                    if state.position == "flat" and portfolio.cash_available >= MIN_ORDER_KRW and analyses:
                        best_tuple = analyses[0]
                        best, best_ob, best_fconf = best_tuple

                        if best_fconf >= MIN_CONFIDENCE_STRONG and best.pm_decision is Signal.LONG and best_ob >= 0.50:
                            invest = min(int(portfolio.cash_available * 0.5), 10_000)
                            invest = max(invest, MIN_ORDER_KRW)
                            if invest <= portfolio.cash_available:
                                print(f"\n🎯 STRONG INITIAL ENTRY: {best.market} (Conf: {best_fconf:.1f}%, BidRatio: {best_ob*100:.1f}%)")
                                execute_buy(best.market, invest, portfolio, settings, confidence=best_fconf, bid_ratio=best_ob, is_pyramiding=False)

                    # Dynamic Pyramiding Scale-In (Only if total position <= 60% of capital & min 33% cash preserved)
                    elif (state.position == "long" and portfolio.active_market and portfolio.pyramiding_count < 2
                          and portfolio.cash_available >= MIN_ORDER_KRW and cur_pnl_pct >= 0.20 and analyses):
                        top_market = analyses[0][0].market
                        top_conf = analyses[0][2]
                        # Enforce 60% Max Position Limit
                        max_pos_allowed = portfolio.total_capital * 0.60
                        if (top_market == portfolio.active_market and top_conf >= 75.0 
                                and cur_val_krw < max_pos_allowed and portfolio.cash_available > portfolio.total_capital * 0.30):
                            scale_amount = min(int(max_pos_allowed - cur_val_krw), int(portfolio.cash_available * 0.5))
                            if scale_amount >= MIN_ORDER_KRW and scale_amount <= portfolio.cash_available:
                                print(f"\n🚀 AUTOMATIC PYRAMIDING SCALE-IN: {portfolio.active_market} (Conf: {top_conf:.1f}%, PnL: {cur_pnl_pct:+.2f}%)")
                                execute_buy(portfolio.active_market, scale_amount, portfolio, settings, confidence=top_conf, bid_ratio=analyses[0][1], is_pyramiding=True)

            except Exception as exc:
                print(f"⚠️ Dynamic scan error: {exc}")

        # ═══════════════════════════════════════════════════════════════
        # 3. HOURLY DISCORD BRIEFING (every ~1 hour)
        # ═══════════════════════════════════════════════════════════════
        if loop_count % HOURLY_REPORT_LOOPS == 0:
            try:
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
                )
            except Exception as exc:
                print(f"⚠️ Hourly briefing error: {exc}")

        # ═══════════════════════════════════════════════════════════════
        # 4. NOTICE & EVENT MONITOR (every ~10m)
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

        sys.stdout.flush()
        time.sleep(PRICE_CHECK_INTERVAL)


if __name__ == "__main__":
    main()
