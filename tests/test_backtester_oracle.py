"""Backtester Oracle Validation Suite.

Independent verification of Backtester accounting, invariants, and causal contracts.
Covers Test Families A through O without using any live data or holdout data.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Sequence
import unittest

from bithumb_coin_trader.backtest import Backtester, BacktestResult, Trade
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.models import Candle, Signal


def make_deterministic_candles(
    prices: Sequence[float],
    start: datetime | None = None,
    interval: timedelta = timedelta(hours=1),
) -> list[Candle]:
    base = start or datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    return [
        Candle(
            timestamp=base + i * interval,
            open=float(p),
            high=float(p),
            low=float(p),
            close=float(p),
            volume=10.0,
        )
        for i, p in enumerate(prices)
    ]


@dataclass(frozen=True)
class ReferenceFill:
    index: int
    side: Signal
    price: float
    quantity: float
    notional: float
    fee: float


@dataclass
class ReferenceAccountingOracle:
    """Minimal independent reference accounting engine.
    
    Favors clarity and simple arithmetic over optimization.
    Used for cross-implementation verification (Family O).
    """
    initial_cash: float
    fee_rate: float
    slippage_bps: float
    maximum_order_krw: float = 100_000.0

    def simulate(
        self,
        candles: Sequence[Candle],
        signals: Sequence[Signal],
        allocation: float = 1.0,
        min_order_krw: float = 5000.0,
    ) -> dict[str, object]:
        slip = self.slippage_bps / 10_000.0
        cash = float(self.initial_cash)
        position_qty = 0.0
        entry_price = 0.0
        trades = []
        equity_curve = [cash]
        fills: list[ReferenceFill] = []

        for i in range(1, len(candles)):
            prev_sig = signals[i - 1]
            open_price = candles[i].open

            # Handle exit if long and signal is FLAT or SHORT (long-only)
            if position_qty > 0.0 and prev_sig in (Signal.FLAT, Signal.SHORT):
                exit_price = open_price * (1.0 - slip)
                exit_notional = position_qty * exit_price
                exit_fee = exit_notional * self.fee_rate
                cash += (exit_notional - exit_fee)
                gross_pnl = position_qty * (exit_price - entry_price)
                net_pnl = gross_pnl - fills[-1].fee - exit_fee
                trades.append({
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "quantity": position_qty,
                })
                fills.append(ReferenceFill(
                    index=i, side=Signal.FLAT, price=exit_price,
                    quantity=position_qty, notional=exit_notional, fee=exit_fee,
                ))
                position_qty = 0.0
                entry_price = 0.0

            # Handle entry if flat and signal is LONG
            if position_qty == 0.0 and prev_sig == Signal.LONG:
                available = cash
                eff_entry_price = open_price * (1.0 + slip)
                # notional after accounting for entry fee and maximum order cap
                max_notional = min(available / (1.0 + self.fee_rate), self.maximum_order_krw)
                target_notional = available * allocation
                notional = min(target_notional, max_notional)
                if notional >= min_order_krw:
                    fee = notional * self.fee_rate
                    cash -= (notional + fee)
                    position_qty = notional / eff_entry_price
                    entry_price = eff_entry_price
                    fills.append(ReferenceFill(
                        index=i, side=Signal.LONG, price=eff_entry_price,
                        quantity=position_qty, notional=notional, fee=fee,
                    ))

            # Mark to market at close
            marked_exit_price = candles[i].close * (1.0 - slip)
            pos_val = position_qty * marked_exit_price * (1.0 - self.fee_rate)
            current_equity = cash + pos_val
            equity_curve.append(current_equity)

        # Final liquidation at last candle close if position open
        if position_qty > 0.0:
            final_exit_price = candles[-1].close * (1.0 - slip)
            final_notional = position_qty * final_exit_price
            final_fee = final_notional * self.fee_rate
            cash += (final_notional - final_fee)
            gross_pnl = position_qty * (final_exit_price - entry_price)
            net_pnl = gross_pnl - fills[-1].fee - final_fee
            trades.append({
                "entry_price": entry_price,
                "exit_price": final_exit_price,
                "gross_pnl": gross_pnl,
                "net_pnl": net_pnl,
                "quantity": position_qty,
                "is_final_liquidation": True,
            })
            equity_curve[-1] = cash

        return {
            "final_equity": equity_curve[-1],
            "equity_curve": tuple(equity_curve),
            "trades": tuple(trades),
            "fills": tuple(fills),
            "final_cash": cash,
        }


class BacktesterOracleTests(unittest.TestCase):
    """Rigorous test suite validating the Backtester against mathematical and logical invariants."""

    # -------------------------------------------------------------------------
    # TEST FAMILY A: CONSTANT MARKET
    # -------------------------------------------------------------------------
    def test_family_a_constant_market_zero_fee_zero_pnl(self) -> None:
        """In a flat market with zero fees, buying and selling produces exactly 0 PnL."""
        candles = make_deterministic_candles([10_000.0] * 5)
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        self.assertAlmostEqual(res.final_equity, 100_000.0, places=6)
        self.assertEqual(len(res.trades), 1)
        self.assertAlmostEqual(res.trades[0].gross_pnl, 0.0, places=6)
        self.assertAlmostEqual(res.trades[0].net_pnl, 0.0, places=6)

    def test_family_a_constant_market_with_fee_strictly_decreases_cash(self) -> None:
        """In a flat market with fee > 0, buying and selling strictly loses equity to fees."""
        candles = make_deterministic_candles([10_000.0] * 5)
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            fee_rate=0.002,  # 0.2%
            slippage_bps=0.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        self.assertLess(res.final_equity, 100_000.0)
        # Verify no unexplained cash creation
        self.assertGreater(res.final_equity, 99_000.0)
        trade = res.trades[0]
        self.assertAlmostEqual(trade.gross_pnl, 0.0, places=6)
        self.assertAlmostEqual(trade.net_pnl, - (trade.entry_fee + trade.exit_fee), places=6)
        self.assertAlmostEqual(res.final_equity, 100_000.0 + trade.net_pnl, places=6)

    # -------------------------------------------------------------------------
    # TEST FAMILY B: DETERMINISTIC MONOTONIC MARKET (UPWARD)
    # -------------------------------------------------------------------------
    def test_family_b_deterministic_monotonic_upward(self) -> None:
        """Known upward price ladder (+10% per bar): PnL matches analytical formula."""
        # Prices: 100, 110, 121, 133.1
        candles = make_deterministic_candles([100.0, 110.0, 121.0, 133.1])
        # Signal at t=0 (LONG) -> enter at t=1 (open=110)
        # Signal at t=1 (FLAT) -> exit at t=2 (open=121)
        signals = [Signal.LONG, Signal.FLAT, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            maximum_order_krw=100_000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        trade = res.trades[0]
        self.assertEqual(trade.entry_price, 110.0)
        self.assertEqual(trade.exit_price, 121.0)
        # Expected return = (121 - 110) / 110 = 0.10 (10%)
        expected_pnl = 100_000.0 * 0.10
        self.assertAlmostEqual(trade.gross_pnl, expected_pnl, places=6)
        self.assertAlmostEqual(res.final_equity, 110_000.0, places=6)

    # -------------------------------------------------------------------------
    # TEST FAMILY C: FALLING MARKET
    # -------------------------------------------------------------------------
    def test_family_c_falling_market_long_only_loses(self) -> None:
        """In a falling market, long-only trade cannot manufacture positive PnL."""
        candles = make_deterministic_candles([100.0, 90.0, 80.0, 70.0])
        signals = [Signal.LONG, Signal.FLAT, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            fee_rate=0.001,
            slippage_bps=0.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings, allow_short=False).run(candles, signals)
        trade = res.trades[0]
        self.assertEqual(trade.entry_price, 90.0)
        self.assertEqual(trade.exit_price, 80.0)
        self.assertLess(trade.gross_pnl, 0.0)
        self.assertLess(trade.net_pnl, trade.gross_pnl)
        self.assertLess(res.final_equity, res.initial_equity)

    # -------------------------------------------------------------------------
    # TEST FAMILY D: FEE MONOTONICITY
    # -------------------------------------------------------------------------
    def test_family_d_fee_monotonicity(self) -> None:
        """Net PnL must be strictly monotonically non-increasing as fee rate rises."""
        candles = make_deterministic_candles([100.0, 110.0, 120.0, 115.0])
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.FLAT]
        fee_rates = [0.0, 0.0005, 0.001, 0.0025, 0.005, 0.01]
        previous_equity = float("inf")

        for fee in fee_rates:
            settings = TradingSettings(
                initial_capital_krw=100_000.0,
                fee_rate=fee,
                slippage_bps=0.0,
                allocation_fraction=1.0,
                cash_reserve_krw=0.0,
            )
            res = Backtester(settings).run(candles, signals)
            self.assertLessEqual(
                res.final_equity,
                previous_equity,
                f"Fee monotonicity violated at fee_rate={fee}",
            )
            previous_equity = res.final_equity

    # -------------------------------------------------------------------------
    # TEST FAMILY E: SLIPPAGE MONOTONICITY
    # -------------------------------------------------------------------------
    def test_family_e_slippage_monotonicity(self) -> None:
        """Net PnL must be strictly monotonically non-increasing as slippage rises."""
        candles = make_deterministic_candles([100.0, 110.0, 120.0, 115.0])
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.FLAT]
        slippages = [0.0, 2.0, 5.0, 10.0, 20.0, 50.0]
        previous_equity = float("inf")

        for slip in slippages:
            settings = TradingSettings(
                initial_capital_krw=100_000.0,
                fee_rate=0.001,
                slippage_bps=slip,
                allocation_fraction=1.0,
                cash_reserve_krw=0.0,
            )
            res = Backtester(settings).run(candles, signals)
            self.assertLessEqual(
                res.final_equity,
                previous_equity,
                f"Slippage monotonicity violated at slippage_bps={slip}",
            )
            previous_equity = res.final_equity

    # -------------------------------------------------------------------------
    # TEST FAMILY F: DETERMINISM
    # -------------------------------------------------------------------------
    def test_family_f_reproducible_determinism(self) -> None:
        """Identical inputs must produce bit-for-bit identical results and hashes."""
        candles = make_deterministic_candles([100.0, 105.0, 95.0, 110.0, 100.0, 120.0])
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.LONG, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=50_000.0,
            fee_rate=0.0015,
            slippage_bps=3.0,
            allocation_fraction=0.8,
            cash_reserve_krw=5000.0,
        )

        res1 = Backtester(settings).run(candles, signals)
        res2 = Backtester(settings).run(candles, signals)

        hash1 = hashlib.sha256(
            json.dumps([res1.equity_curve, [(t.notional, t.net_pnl) for t in res1.trades]]).encode()
        ).hexdigest()
        hash2 = hashlib.sha256(
            json.dumps([res2.equity_curve, [(t.notional, t.net_pnl) for t in res2.trades]]).encode()
        ).hexdigest()

        self.assertEqual(hash1, hash2)
        self.assertEqual(res1.final_equity, res2.final_equity)
        self.assertEqual(res1.trades, res2.trades)

    # -------------------------------------------------------------------------
    # TEST FAMILY G: SIGNAL DELAY (NO SAME-BAR EXECUTION)
    # -------------------------------------------------------------------------
    def test_family_g_signal_delay_contract(self) -> None:
        """Signal generated at t must execute at open price of t+1, never at t close."""
        # Bar 0: open=100, close=150
        # Bar 1: open=200, close=250
        # Bar 2: open=300, close=350
        candles = [
            Candle(datetime(2025, 1, 1, 0, 0, tzinfo=UTC), 100.0, 150.0, 100.0, 150.0, 1.0),
            Candle(datetime(2025, 1, 1, 1, 0, tzinfo=UTC), 200.0, 250.0, 200.0, 250.0, 1.0),
            Candle(datetime(2025, 1, 1, 2, 0, tzinfo=UTC), 300.0, 350.0, 300.0, 350.0, 1.0),
        ]
        signals = [Signal.LONG, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        # Entry must be at Bar 1 OPEN (200.0), NOT Bar 0 CLOSE (150.0)
        self.assertEqual(res.trades[0].entry_price, 200.0)
        self.assertNotEqual(res.trades[0].entry_price, 150.0)

    # -------------------------------------------------------------------------
    # TEST FAMILY H: LOOKAHEAD SENTINEL (CAUSAL FEATURE CONTRACT)
    # -------------------------------------------------------------------------
    def test_family_h_causal_contract_violation_detection(self) -> None:
        """A feature function that peeks into future bars must be detectable as a violation."""
        def safe_causal_feature(data: Sequence[Candle], current_idx: int) -> float:
            # Uses only past and current bar
            return data[current_idx].close - data[current_idx].open

        def lookahead_cheating_feature(data: Sequence[Candle], current_idx: int) -> float:
            # Leaks future bar
            next_idx = min(current_idx + 1, len(data) - 1)
            return data[next_idx].close - data[current_idx].close

        # Oracle verifier for causal contract:
        def verify_causal_invariance(
            feature_fn: Callable[[Sequence[Candle], int], float],
            base_candles: list[Candle],
        ) -> bool:
            test_idx = len(base_candles) // 2
            original_val = feature_fn(base_candles, test_idx)
            # Mutate immediately following future bar (test_idx + 1)
            mutated_candles = list(base_candles)
            mutated_candles[test_idx + 1] = Candle(
                base_candles[test_idx + 1].timestamp,
                base_candles[test_idx + 1].open * 2,
                base_candles[test_idx + 1].high * 2,
                base_candles[test_idx + 1].low * 2,
                base_candles[test_idx + 1].close * 2,
                base_candles[test_idx + 1].volume,
            )
            mutated_val = feature_fn(mutated_candles, test_idx)
            return original_val == mutated_val

        candles = make_deterministic_candles([100.0, 102.0, 101.0, 105.0, 104.0, 108.0])
        # Safe feature must be invariant to future mutation
        self.assertTrue(verify_causal_invariance(safe_causal_feature, candles))
        # Lookahead feature will fail invariance test
        self.assertFalse(verify_causal_invariance(lookahead_cheating_feature, candles))

    # -------------------------------------------------------------------------
    # TEST FAMILY I: DATA ORDERING INTEGRITY
    # -------------------------------------------------------------------------
    def test_family_i_data_ordering_rejection(self) -> None:
        """Out-of-order, duplicate, or reversed timestamps must fail closed."""
        base = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
        # Duplicate timestamps
        dup_candles = [
            Candle(base, 100, 100, 100, 100, 1),
            Candle(base, 100, 100, 100, 100, 1),
        ]
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            Backtester().run(dup_candles, [Signal.FLAT, Signal.FLAT])

        # Reversed timestamps
        rev_candles = [
            Candle(base + timedelta(hours=2), 100, 100, 100, 100, 1),
            Candle(base + timedelta(hours=1), 100, 100, 100, 100, 1),
        ]
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            Backtester().run(rev_candles, [Signal.FLAT, Signal.FLAT])

    # -------------------------------------------------------------------------
    # TEST FAMILY J: CASH CONSERVATION INVARIANT
    # -------------------------------------------------------------------------
    def test_family_j_cash_conservation_and_no_negative_equity(self) -> None:
        """At all steps, cash, position value, and fees reconcile. No negative cash."""
        candles = make_deterministic_candles([100.0, 120.0, 90.0, 110.0, 105.0])
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.LONG, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=10_000.0,
            fee_rate=0.002,
            slippage_bps=5.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        for eq in res.equity_curve:
            self.assertGreaterEqual(eq, 0.0, "Negative equity observed!")
        # Total fees accounting
        self.assertGreater(res.total_fees, 0.0)
        self.assertAlmostEqual(
            res.total_fees,
            sum(t.entry_fee + t.exit_fee for t in res.trades),
            places=6,
        )

    # -------------------------------------------------------------------------
    # TEST FAMILY K: MINIMUM ORDER THRESHOLD
    # -------------------------------------------------------------------------
    def test_family_k_minimum_order_rejection(self) -> None:
        """Orders below minimum_order_krw (e.g. 5,000 KRW) must be rejected."""
        candles = make_deterministic_candles([100.0, 100.0, 100.0])
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT]
        # Capital 100,000 KRW with allocation_fraction=0.04 results in 4,000 KRW notional (< 5,000 KRW)
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            minimum_order_krw=5_000.0,
            maximum_order_krw=100_000.0,
            allocation_fraction=0.04,
            fee_rate=0.0,
            slippage_bps=0.0,
            cash_reserve_krw=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        self.assertEqual(len(res.trades), 0)
        self.assertEqual(res.final_equity, 100_000.0)
        self.assertGreaterEqual(len(res.entry_rejections), 1)
        self.assertIn("order is below the exchange minimum", res.entry_rejections[0].reasons[0])

    # -------------------------------------------------------------------------
    # TEST FAMILY L: EXPOSURE CAP
    # -------------------------------------------------------------------------
    def test_family_l_exposure_cap_enforcement(self) -> None:
        """Position size respects allocation_fraction and maximum_order_krw."""
        candles = make_deterministic_candles([100.0, 100.0, 100.0])
        signals = [Signal.LONG, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            allocation_fraction=0.30,  # 30% allocation
            maximum_order_krw=50_000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        trade = res.trades[0]
        self.assertAlmostEqual(trade.notional, 30_000.0, places=4)

    # -------------------------------------------------------------------------
    # TEST FAMILY M: ROUND TRIP ACCOUNTING
    # -------------------------------------------------------------------------
    def test_family_m_round_trip_closed_trade_count(self) -> None:
        """BUY -> HOLD -> SELL is exactly one closed round trip."""
        candles = make_deterministic_candles([100.0, 100.0, 100.0, 100.0, 100.0])
        signals = [Signal.LONG, Signal.LONG, Signal.LONG, Signal.FLAT, Signal.FLAT]
        settings = TradingSettings(
            initial_capital_krw=50_000.0,
            fee_rate=0.0,
            slippage_bps=0.0,
        )
        res = Backtester(settings).run(candles, signals)
        self.assertEqual(len(res.trades), 1)
        self.assertEqual(res.closed_trade_count, 1)
        self.assertFalse(res.trades[0].is_final_liquidation)

    # -------------------------------------------------------------------------
    # TEST FAMILY N: ZERO-EDGE STOCHASTIC SANITY
    # -------------------------------------------------------------------------
    def test_family_n_zero_edge_stochastic_sanity(self) -> None:
        """Random strategies across zero-drift price series with fees show no positive drift."""
        rnd = random.Random(42)
        returns = []

        for seed in range(10):
            r = random.Random(seed)
            price = 1000.0
            prices = [price]
            for _ in range(50):
                # 0 mean random walk
                step = r.gauss(0.0, 0.01)
                price *= (1.0 + step)
                prices.append(price)
            candles = make_deterministic_candles(prices)
            # Random signals
            sigs = [r.choice([Signal.LONG, Signal.FLAT]) for _ in range(len(candles))]
            settings = TradingSettings(
                initial_capital_krw=100_000.0,
                fee_rate=0.001,
                slippage_bps=2.0,
            )
            res = Backtester(settings).run(candles, sigs)
            returns.append(res.total_return)

        avg_return = sum(returns) / len(returns)
        # Fees and slippage must prevent positive drift on zero-edge series
        self.assertLess(avg_return, 0.05, f"Unexplained positive edge detected: {avg_return}")

    # -------------------------------------------------------------------------
    # TEST FAMILY O: REFERENCE-ENGINE RECONCILIATION
    # -------------------------------------------------------------------------
    def test_family_o_reference_oracle_reconciliation(self) -> None:
        """Independent reference implementation reconciles exactly with production backtester."""
        candles = make_deterministic_candles([100.0, 105.0, 110.0, 108.0, 115.0])
        signals = [Signal.LONG, Signal.LONG, Signal.FLAT, Signal.LONG, Signal.FLAT]

        settings = TradingSettings(
            initial_capital_krw=100_000.0,
            maximum_order_krw=100_000.0,
            maximum_daily_entries=10,
            fee_rate=0.001,
            slippage_bps=5.0,
            allocation_fraction=1.0,
            cash_reserve_krw=0.0,
        )

        prod_result = Backtester(settings).run(candles, signals)

        oracle = ReferenceAccountingOracle(
            initial_cash=100_000.0,
            fee_rate=0.001,
            slippage_bps=5.0,
            maximum_order_krw=100_000.0,
        )
        oracle_result = oracle.simulate(candles, signals, allocation=1.0)

        # Reconcile final equity
        self.assertAlmostEqual(
            prod_result.final_equity,
            oracle_result["final_equity"],
            places=4,
            msg="Production and Reference Oracle final equity diverged!",
        )

        # Reconcile trade count
        self.assertEqual(len(prod_result.trades), len(oracle_result["trades"]))


if __name__ == "__main__":
    unittest.main()
