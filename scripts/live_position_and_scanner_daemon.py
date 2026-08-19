#!/usr/bin/env python3
"""Daemon for managing active live positions and scanning for optimal setups."""

import time
import os
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bithumb_coin_trader.config import TradingMode, TradingSettings
from bithumb_coin_trader.data import fetch_minute_candles
from bithumb_coin_trader.execution import BithumbExecutor, TradeIntent, plan_execution, LIVE_CONFIRMATION_TOKEN
from bithumb_coin_trader.mcp_client import McpStdioClient, LIVE_COMMAND
from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.risk import RiskContext, evaluate_pretrade
from bithumb_coin_trader.state import load_state, save_state
from scripts.scan_and_trade import DEFAULT_MARKETS, analyze_market

STATE_PATH = PROJECT_ROOT / "state" / "live.json"


def main():
    print("=" * 80)
    print(" 🛡️ BITHUMB LIVE POSITION MANAGER & MULTI-MARKET SCANNER DAEMON")
    print("=" * 80)
    print("Monitoring active positions and market opportunities every 60s...")

    settings = TradingSettings(
        initial_capital_krw=20_000,
        mode=TradingMode.LIVE,
        live_trading_enabled=True,
        minimum_order_krw=5_000,
        cash_reserve_krw=5_000,
    )

    entry_price_est = 13_550.0  # KRW-LINK entry price
    stop_loss_price = entry_price_est * 0.98  # -2.0%
    take_profit_price = entry_price_est * 1.05  # +5.0%
    highest_price = entry_price_est

    loop_count = 0
    while True:
        loop_count += 1
        state = load_state(STATE_PATH)
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')

        # 1. Manage Active Position (if long)
        if state.position == "long" and Decimal(state.position_volume) > 0:
            try:
                candles = fetch_minute_candles("KRW-LINK", 1, 10)
                current_price = candles[-1].close
                vol = Decimal(state.position_volume)
                val_krw = float(vol) * current_price
                ret_pct = (current_price - entry_price_est) / entry_price_est * 100.0

                if current_price > highest_price:
                    highest_price = current_price

                trailing_stop_price = highest_price * 0.985

                print(f"\n[{now_str}] 📈 Active Position: KRW-LINK | Qty: {vol:.4f} | CurPrice: {current_price:,.0f} KRW | PnL: {ret_pct:+.2f}% ({val_krw:,.0f} KRW)")
                print(f"  - Risk Bounds: StopLoss: {stop_loss_price:,.0f} | TakeProfit: {take_profit_price:,.0f} | TrailingStop: {trailing_stop_price:,.0f}")

                # Check Exit Trigger
                trigger_exit = False
                exit_reason = ""
                if current_price <= stop_loss_price:
                    trigger_exit = True
                    exit_reason = f"Stop-Loss hit ({current_price} <= {stop_loss_price})"
                elif current_price >= take_profit_price:
                    trigger_exit = True
                    exit_reason = f"Take-Profit hit ({current_price} >= {take_profit_price})"
                elif highest_price > entry_price_est * 1.01 and current_price <= trailing_stop_price:
                    trigger_exit = True
                    exit_reason = f"Trailing-Stop hit ({current_price} <= {trailing_stop_price})"

                if trigger_exit:
                    print(f"\n🚨 EXIT SIGNAL TRIGGERED: {exit_reason}")
                    print("Executing Market Sell to protect capital...")
                    intent = TradeIntent(
                        market="KRW-LINK",
                        target=Signal.FLAT,
                        base_volume=vol,
                        reason=exit_reason,
                    )
                    plan = plan_execution(intent, current=Signal.LONG)
                    risk_context = RiskContext(
                        requested_side=Signal.FLAT,
                        requested_notional_krw=int(val_krw),
                        current_equity_krw=20_000.0,
                        start_of_day_equity_krw=20_000.0,
                        peak_equity_krw=20_000.0,
                        daily_entries=0,
                        data_is_fresh=True,
                        reference_price_krw=current_price,
                    )
                    with McpStdioClient(LIVE_COMMAND) as client:
                        executor = BithumbExecutor(client=client, state_path=STATE_PATH, settings=settings)
                        res = executor.execute(
                            plan,
                            risk_context=risk_context,
                            bot_state=state,
                            confirmation_token=LIVE_CONFIRMATION_TOKEN,
                        )
                        print(f"🎉 Exit Order Submitted: {res}")
            except Exception as exc:
                print(f"⚠️ Position management check error: {exc}")

        # 2. Every 5 loops (approx 15 seconds), Scan 10-coin market
        if loop_count % 5 == 1:
            try:
                print(f"\n[{now_str}] 🔍 Background Scanning 10 Markets for New Setups...")
                analyses = []
                for m in DEFAULT_MARKETS:
                    res = analyze_market(m)
                    if res:
                        analyses.append(res)
                if analyses:
                    analyses.sort(key=lambda x: x.ace_confidence, reverse=True)
                    top = analyses[0]
                    print(f"  ⭐ Top Market Setup: {top.market} (Conf: {top.ace_confidence}%, Status: {top.recommendation})")
            except Exception as exc:
                print(f"⚠️ Scanner check error: {exc}")

        sys.stdout.flush()
        time.sleep(3)


if __name__ == "__main__":
    main()
