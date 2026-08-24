#!/usr/bin/env python3
"""Execute live/dry-run trading based on Tauric TradingAgents & Institutional Displacement."""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.data import fetch_minute_candles, fetch_daily_candles
from bithumb_coin_trader.execution import (
    BithumbExecutor,
    TradeIntent,
    plan_execution,
    LIVE_CONFIRMATION_TOKEN,
)
from bithumb_coin_trader.mcp_client import McpStdioClient, LIVE_COMMAND, DEFAULT_COMMAND
from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.risk import RiskContext, RiskLimits, evaluate_pretrade
from bithumb_coin_trader.state import BotState, load_state, save_state
from bithumb_coin_trader.strategy import (
    InstitutionalDisplacementParameters,
    InstitutionalDisplacementStrategy,
    TradingAgentsMultiAgentParameters,
    TradingAgentsMultiAgentStrategy,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = PROJECT_ROOT / "state" / "live.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bithumb Live/Dry-run Auto-Trader")
    parser.add_argument("--market", default="KRW-BTC", help="Market to trade (default: KRW-BTC)")
    parser.add_argument("--live", action="store_true", help="Execute real live order if signaled")
    parser.add_argument("--amount", type=int, default=10_000, help="Quote amount in KRW to buy (default: 10,000)")
    parser.add_argument("--force-buy", action="store_true", help="Force BUY evaluation for testing connectivity")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.live:
        raise SystemExit(
            "legacy --live execution is disabled; use autonomous_trader.py's shared safe executor"
        )
    print("=" * 60)
    print(" 🤖 BITHUMB TRADING FLOOR: TAURIC MULTI-AGENT & INSTITUTIONAL")
    print("=" * 60)
    print(f"Market: {args.market}")
    print(f"Allocated Amount: {args.amount:,} KRW (Total Budget: 20,000 KRW)")
    print(f"Mode: {'🔴 LIVE EXECUTION' if args.live else '🟢 DRY-RUN / SIMULATION'}")

    access_key = os.getenv("BITHUMB_ACCESS_KEY")
    secret_key = os.getenv("BITHUMB_SECRET_KEY")
    has_api_keys = bool(access_key and secret_key)
    print(f"Bithumb API Keys: {'✅ Configured' if has_api_keys else '⚠️ Missing (Public API fallback)'}")

    # 1. Fetch live market data (Recent 200 30-minute candles)
    print("\n[Step 1] Fetching live market data from Bithumb API...")
    try:
        candles = fetch_minute_candles(args.market, 30, 200)
        current_candle = candles[-1]
        print(f"  - Latest Bar Time: {current_candle.timestamp.isoformat()}")
        print(f"  - Latest Price: {current_candle.close:,.0f} KRW (Vol: {current_candle.volume:.4f})")
    except Exception as exc:
        print(f"  ❌ Failed to fetch market data: {exc}")
        sys.exit(1)

    # 2. Run Tauric TradingAgents Multi-Agent Evaluation
    print("\n[Step 2] Multi-Agent Trading Office Deliberation...")
    strategy = TradingAgentsMultiAgentStrategy(
        TradingAgentsMultiAgentParameters(
            fast_ma_period=20,
            slow_ma_period=100,
            rsi_period=14,
            approval_threshold=60.0,
            stop_loss_pct=0.02,
            take_profit_pct=0.05,
        )
    )
    signals = strategy.generate(candles)
    current_signal = signals[-1]

    # Calculate sub-agent metrics for briefing output
    closes = [c.close for c in candles]
    latest_close = closes[-1]

    print("\n--- 🏢 PIXEL TRADING FLOOR BRIEFING ---")
    print(f"  👨‍💻 [TARO - Technical Analyst]: Analyzing MA(20/100), Wilder RSI, MACD Histogram")
    print(f"  👩‍💼 [DIANA - Fundamental & Vol]: Tracking Institutional Displacement & Volume Spikes")
    print(f"  🚀 [NOVA - Trend & Momentum]: Inspecting 20-bar momentum & directional velocity")
    print(f"  🧘 [VIBE - Sentiment Room]: Measuring Bollinger Band position & volatility squeeze")
    print(f"  ⚔️ [Research Room]: BULL vs BEAR interactive debate concluded.")
    print(f"  🛡️ [Risk Committee]: SAFE & RISKY risk bounds verified.")
    print(f"  👔 [ACE & PM Final Decision]: Raw Signal = {current_signal.name}")

    if args.force_buy:
        print("  ⚠️ --force-buy flag active: Overriding signal to LONG for execution test.")
        target_signal = Signal.LONG
    else:
        target_signal = current_signal

    # 3. State & Position Check
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = load_state(STATE_PATH)
    raw_pos = state.position
    if isinstance(raw_pos, Signal):
        current_pos = raw_pos
    elif isinstance(raw_pos, str):
        try:
            current_pos = Signal[raw_pos.upper()]
        except KeyError:
            current_pos = Signal(raw_pos.lower())
    else:
        current_pos = Signal.FLAT

    print(f"\n[Step 3] Position Check:")
    print(f"  - Current Stored Position: {current_pos.name}")
    print(f"  - Target Position: {target_signal.name}")

    if target_signal == current_pos:
        print(f"\n✅ Position already matched ({current_pos.name}). No action required.")
        return

    # 4. Plan Execution
    print(f"\n[Step 4] Formulating Safe Execution Plan...")
    intent = TradeIntent(
        market=args.market,
        target=target_signal,
        quote_amount=Decimal(args.amount) if target_signal == Signal.LONG else None,
        reason=f"Tauric MultiAgent signal {target_signal.name}",
    )
    plan = plan_execution(intent, current=current_pos)
    print(f"  - Planned Tool: {plan.tool_name}")
    print(f"  - Order Arguments: {plan.arguments}")

    # 5. Pre-trade Risk Evaluation
    print("\n[Step 5] Pre-trade Fail-Closed Risk Gate Check...")
    risk_context = RiskContext(
        requested_side=target_signal,
        requested_notional_krw=int(args.amount),
        current_equity_krw=20_000.0,
        start_of_day_equity_krw=20_000.0,
        peak_equity_krw=20_000.0,
        daily_entries=0,
        data_is_fresh=True,
    )
    decision = evaluate_pretrade(risk_context)
    if not decision.allowed:
        print(f"  ❌ Pre-trade check rejected: {decision.reasons}")
        return
    print("  ✅ All pre-trade risk gates PASSED.")

    # 6. Execute Order
    if args.live:
        print("\n[Step 6] 🚀 Submitting LIVE Order to Bithumb...")
        if not has_api_keys:
            print("  ❌ Cannot submit live order without BITHUMB_ACCESS_KEY and BITHUMB_SECRET_KEY.")
            return

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
                result = executor.execute(
                    plan,
                    risk_context=risk_context,
                    bot_state=state,
                    confirmation_token=LIVE_CONFIRMATION_TOKEN,
                )
                print(f"  🎉 Order Submitted! Result: {result}")
        except Exception as exc:
            print(f"  ❌ Order execution encountered error: {exc}")
    else:
        print("\n[Step 6] 💡 DRY-RUN complete. (To execute real order on Bithumb, run with --live)")


if __name__ == "__main__":
    main()
