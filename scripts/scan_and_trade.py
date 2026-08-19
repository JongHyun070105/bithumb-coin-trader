#!/usr/bin/env python3
"""Scan top Bithumb KRW markets, rank by Tauric Multi-Agent & Institutional Score, and trade the best candidate."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.data import fetch_minute_candles
from bithumb_coin_trader.execution import (
    BithumbExecutor,
    TradeIntent,
    plan_execution,
    LIVE_CONFIRMATION_TOKEN,
)
from bithumb_coin_trader.indicators import (
    bollinger_bands,
    institutional_displacement_signals,
    macd,
    simple_moving_average,
    wilder_rsi,
)
from bithumb_coin_trader.mcp_client import McpStdioClient, LIVE_COMMAND
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.risk import RiskContext, evaluate_pretrade
from bithumb_coin_trader.state import BotState, load_state, save_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = PROJECT_ROOT / "state" / "live.json"

DEFAULT_MARKETS = [
    "KRW-BTC",
    "KRW-ETH",
    "KRW-SOL",
    "KRW-XRP",
    "KRW-DOGE",
    "KRW-ADA",
    "KRW-AVAX",
    "KRW-LINK",
    "KRW-NEAR",
    "KRW-SUI",
]


@dataclass
class MarketAnalysis:
    market: str
    latest_price: float
    price_change_24h_pct: float
    bull_shift: bool
    bear_shift: bool
    rsi: float
    taro_score: float
    diana_score: float
    nova_score: float
    vibe_score: float
    ace_confidence: float
    safe_approved: bool
    pm_decision: Signal
    recommendation: str


def analyze_market(market: str) -> MarketAnalysis | None:
    try:
        candles = fetch_minute_candles(market, 30, 100)
        if len(candles) < 50:
            return None
    except Exception as exc:
        print(f"  ⚠️ [{market}] Failed to fetch candles: {exc}")
        return None

    opens = [c.open for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]

    current_price = closes[-1]
    # 24h change (48 bars)
    ret_24h = (current_price - closes[-48]) / closes[-48] * 100.0 if len(closes) >= 48 else 0.0

    fast_ma = simple_moving_average(closes, 20)
    slow_ma = simple_moving_average(closes, 50)
    rsi_vals = wilder_rsi(closes, 14)
    _, _, hist = macd(closes, 12, 26, 9)
    _, bb_upper, bb_lower = bollinger_bands(closes, 20, 2.0)

    bull_shifts, bear_shifts, _ = institutional_displacement_signals(
        opens, highs, lows, closes, volumes, vol_period=20, vol_multiplier=2.0, min_body_pct=45.0
    )

    f_ma = fast_ma[-1] or current_price
    s_ma = slow_ma[-1] or current_price
    r_val = rsi_vals[-1] or 50.0
    h_val = hist[-1] or 0.0
    bull = bull_shifts[-1]
    bear = bear_shifts[-1]

    # TARO (Technical): MA alignment, RSI momentum, MACD
    taro_score = 0.0
    if f_ma > s_ma and current_price > s_ma:
        taro_score += 45.0
    if 40.0 <= r_val <= 70.0:
        taro_score += 30.0
    if h_val > 0:
        taro_score += 25.0

    # DIANA (Fundamental & Institutional Volume)
    diana_score = 50.0
    if bull:
        diana_score = 100.0
    elif bear:
        diana_score = 0.0

    # NOVA (Trend Velocity)
    ret_20 = (current_price - closes[-20]) / closes[-20] if len(closes) >= 20 else 0.0
    nova_score = 50.0 + min(max(ret_20 * 500.0, -40.0), 40.0)

    # VIBE (Sentiment & Volatility)
    vibe_score = 50.0
    if bb_lower[-1] is not None and bb_upper[-1] is not None:
        bb_range = bb_upper[-1] - bb_lower[-1]
        if bb_range > 0:
            pos_in_bb = (current_price - bb_lower[-1]) / bb_range
            vibe_score = min(max(pos_in_bb * 100.0, 0.0), 100.0)

    # Bull vs Bear & ACE Confidence
    bull_arg = (taro_score * 0.40) + (diana_score * 0.30) + (nova_score * 0.15) + (vibe_score * 0.15)
    ace_confidence = bull_arg
    if bull and f_ma > s_ma:
        ace_confidence += 10.0

    # Risk Committee (SAFE)
    safe_approved = r_val < 72.0 and not bear and current_price > (s_ma * 0.98)

    # PM Decision
    if safe_approved and ace_confidence >= 65.0:
        pm_decision = Signal.LONG
        recommendation = "🟢 STRONG BUY"
    elif safe_approved and ace_confidence >= 55.0 and (bull or h_val > 0):
        pm_decision = Signal.LONG
        recommendation = "🟡 BUY"
    else:
        pm_decision = Signal.FLAT
        recommendation = "⚪ HOLD / WATCH"

    return MarketAnalysis(
        market=market,
        latest_price=current_price,
        price_change_24h_pct=round(ret_24h, 2),
        bull_shift=bull,
        bear_shift=bear,
        rsi=round(r_val, 1),
        taro_score=round(taro_score, 1),
        diana_score=round(diana_score, 1),
        nova_score=round(nova_score, 1),
        vibe_score=round(vibe_score, 1),
        ace_confidence=round(ace_confidence, 1),
        safe_approved=safe_approved,
        pm_decision=pm_decision,
        recommendation=recommendation,
    )


def run_scan_and_trade(live: bool, amount: int, force_best: bool) -> None:
    print("=" * 80)
    print(" 📡 BITHUMB MULTI-MARKET SCANNER (TAURIC AGENTS & INSTITUTIONAL DELTA)")
    print("=" * 80)
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Scanning {len(DEFAULT_MARKETS)} major markets...")

    analyses: list[MarketAnalysis] = []
    for m in DEFAULT_MARKETS:
        res = analyze_market(m)
        if res:
            analyses.append(res)

    if not analyses:
        print("❌ Failed to analyze any markets.")
        return

    # Sort by ACE Confidence score (highest first)
    analyses.sort(key=lambda x: x.ace_confidence, reverse=True)

    print("\n" + "-" * 80)
    print(f"{'Market':<10} | {'Price (KRW)':<12} | {'24h %':<7} | {'RSI':<5} | {'TARO':<5} | {'DIANA':<5} | {'Conf':<6} | {'Status':<14}")
    print("-" * 80)
    for a in analyses:
        shift_tag = " [INST 🐋]" if a.bull_shift else ""
        print(f"{a.market:<10} | {a.latest_price:>12,.0f} | {a.price_change_24h_pct:>+6.2f}% | {a.rsi:>5.1f} | {a.taro_score:>5.1f} | {a.diana_score:>5.1f} | {a.ace_confidence:>5.1f}% | {a.recommendation}{shift_tag}")
    print("-" * 80)

    best = analyses[0]
    print(f"\n🏆 Top Ranked Asset: {best.market} (Confidence: {best.ace_confidence}%, Status: {best.recommendation})")

    should_buy = best.pm_decision is Signal.LONG or force_best
    if not should_buy:
        print(f"⏸️ No asset currently qualifies for immediate entry threshold. Continuing observation...")
        return

    print(f"\n🎯 Selected Target for Execution: {best.market}")
    print(f"Allocated Budget: {amount:,} KRW (from total 20,000 KRW)")

    # Formulate Execution Plan
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = load_state(STATE_PATH)
    raw_pos = state.position
    current_pos = Signal.FLAT
    if isinstance(raw_pos, Signal):
        current_pos = raw_pos
    elif isinstance(raw_pos, str):
        try:
            current_pos = Signal[raw_pos.upper()]
        except KeyError:
            current_pos = Signal(raw_pos.lower())

    intent = TradeIntent(
        market=best.market,
        target=Signal.LONG,
        quote_amount=Decimal(amount),
        reason=f"Top Scanner Rank {best.market} (Confidence {best.ace_confidence}%)",
    )
    plan = plan_execution(intent, current=current_pos)
    print(f"  - Planned Tool: {plan.tool_name}")
    print(f"  - Planned Arguments: {plan.arguments}")

    # Risk Check
    risk_context = RiskContext(
        requested_side=Signal.LONG,
        requested_notional_krw=float(amount),
        current_equity_krw=20_000.0,
        start_of_day_equity_krw=20_000.0,
        peak_equity_krw=20_000.0,
        daily_entries=0,
        data_is_fresh=True,
    )
    risk_decision = evaluate_pretrade(risk_context)
    if not risk_decision.allowed:
        print(f"❌ Pre-trade risk gate rejected: {risk_decision.reasons}")
        return
    print("✅ Pre-trade risk checks PASSED.")

    if live:
        print(f"\n🚀 [LIVE ORDER SUBMISSION] Sending buy order for {best.market} ({amount:,} KRW)...")
        settings = TradingSettings(
            initial_capital_krw=20_000,
            mode=TradingMode.LIVE,
            live_trading_enabled=True,
            minimum_order_krw=5_000,
            cash_reserve_krw=5_000,
        )
        try:
            with McpStdioClient(LIVE_COMMAND) as client:
                executor = BithumbExecutor(
                    client=client,
                    state_path=STATE_PATH,
                    settings=settings,
                )
                res = executor.execute(
                    plan,
                    risk_context=risk_context,
                    bot_state=state,
                    confirmation_token=LIVE_CONFIRMATION_TOKEN,
                )
                print(f"🎉 Live Order Executed successfully! Result: {res}")
        except Exception as exc:
            print(f"❌ Order submission error: {exc}")
    else:
        print("\n💡 DRY-RUN complete. Run with --live to execute actual order.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan top Bithumb markets and trade best candidate")
    parser.add_argument("--live", action="store_true", help="Execute real order on Bithumb")
    parser.add_argument("--amount", type=int, default=10_000, help="Quote amount in KRW (default: 10,000)")
    parser.add_argument("--force-best", action="store_true", help="Execute on top-ranked candidate even if HOLD")
    parser.add_argument("--loop", type=int, default=0, help="Run repeatedly every N seconds (0 for single run)")
    args = parser.parse_args()

    if args.loop > 0:
        print(f"🔄 Starting recurring scanner loop (Interval: {args.loop}s)...")
        while True:
            try:
                run_scan_and_trade(args.live, args.amount, args.force_best)
            except Exception as exc:
                print(f"⚠️ Loop exception: {exc}")
            time.sleep(args.loop)
    else:
        run_scan_and_trade(args.live, args.amount, args.force_best)


if __name__ == "__main__":
    main()
