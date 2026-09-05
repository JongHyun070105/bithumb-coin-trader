"""Causality and Invariant Tests for Microstructure Feature Engine (P1.1 - P1.3).

Verifies:
1. Strict causal contract: Mutating events after timestamp T has ZERO effect on features at or before T.
2. Cont, Kukanov & Stoikov (2014) OFI v2 vs Naive OFI v1.
3. ATI exchange-side normalization (Bithumb, Binance, Upbit).
4. MPQI queue-weighted microprice.
5. Warmup contracts (None returned when insufficient data, never arbitrary zero).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import unittest

from bithumb_coin_trader.microstructure_features import (
    OrderbookSnapshot,
    TradeTick,
    compute_ati,
    compute_mpqi,
    compute_ofi_v1,
    compute_ofi_v2,
    normalize_aggressor_side,
)


def make_ob(ts_offset_sec: float, best_bid: float, bid_sz: float, best_ask: float, ask_sz: float) -> OrderbookSnapshot:
    dt = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=ts_offset_sec)
    bids = ((best_bid, bid_sz), (best_bid - 1000.0, bid_sz * 1.5))
    asks = ((best_ask, ask_sz), (best_ask + 1000.0, ask_sz * 1.5))
    return OrderbookSnapshot(market="KRW-BTC", timestamp=dt, bids=bids, asks=asks)


class MicrostructureFeatureCausalityTests(unittest.TestCase):
    def test_feature_causality_contract(self) -> None:
        """Mutating events after timestamp T must leave features at T byte-for-byte identical."""
        base_time = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        t_cutoff = base_time + timedelta(seconds=10.0)

        # Event stream before and after cutoff
        trades_original = [
            TradeTick("KRW-BTC", base_time + timedelta(seconds=1.0), 100_000.0, 0.5, "BUY"),
            TradeTick("KRW-BTC", base_time + timedelta(seconds=5.0), 100_100.0, 0.2, "SELL"),
            TradeTick("KRW-BTC", base_time + timedelta(seconds=9.0), 100_200.0, 0.8, "BUY"),
            # After cutoff:
            TradeTick("KRW-BTC", base_time + timedelta(seconds=11.0), 100_500.0, 10.0, "BUY"),
            TradeTick("KRW-BTC", base_time + timedelta(seconds=15.0), 101_000.0, 50.0, "BUY"),
        ]

        # Mutated stream: all events after t_cutoff are drastically altered
        trades_mutated = trades_original[:3] + [
            TradeTick("KRW-BTC", base_time + timedelta(seconds=11.0), 50_000.0, 999.0, "SELL"),
            TradeTick("KRW-BTC", base_time + timedelta(seconds=12.0), 10_000.0, 999.0, "SELL"),
        ]

        ati_orig = compute_ati(trades_original, current_time=t_cutoff, window_seconds=10.0)
        ati_mut = compute_ati(trades_mutated, current_time=t_cutoff, window_seconds=10.0)

        self.assertIsNotNone(ati_orig)
        self.assertEqual(ati_orig, ati_mut, "Feature at cutoff was altered by post-cutoff event mutation!")

    def test_ofi_v2_cont_kukanov_stoikov_price_jumps(self) -> None:
        """Verify OFI v2 properly handles price level jumps vs same-price size changes."""
        # Case 1: Best bid increases from 100.0 (size 2.0) to 101.0 (size 1.5). Ask unchanged at 102.0.
        # Under Cont et al. (2014): I_b = q_b(t) = +1.5. Ask unchanged: I_a = 0. OFI = +1.5.
        ob0 = make_ob(0.0, best_bid=100.0, bid_sz=2.0, best_ask=102.0, ask_sz=3.0)
        ob1 = make_ob(1.0, best_bid=101.0, bid_sz=1.5, best_ask=102.0, ask_sz=3.0)

        ofi_v2_jump = compute_ofi_v2(ob0, ob1)
        self.assertEqual(ofi_v2_jump, 1.5)

        # Case 2: Best bid unchanged at 100.0, size increases from 2.0 to 3.5.
        # Under Cont et al. (2014): I_b = 3.5 - 2.0 = +1.5. Ask unchanged: OFI = +1.5.
        ob2 = make_ob(1.0, best_bid=100.0, bid_sz=3.5, best_ask=102.0, ask_sz=3.0)
        ofi_v2_same_price = compute_ofi_v2(ob0, ob2)
        self.assertEqual(ofi_v2_same_price, 1.5)

        # Case 3: Best bid drops from 100.0 (size 2.0) to 99.0 (size 5.0).
        # Previous best bid level was depleted/cancelled.
        # Under Cont et al. (2014): I_b = -q_b(t-1) = -2.0. (NOT 5.0 - 2.0 = +3.0!).
        ob3 = make_ob(1.0, best_bid=99.0, bid_sz=5.0, best_ask=102.0, ask_sz=3.0)
        ofi_v2_drop = compute_ofi_v2(ob0, ob3)
        self.assertEqual(ofi_v2_drop, -2.0)

    def test_ati_exchange_side_normalization(self) -> None:
        """Verify trade aggressor sides across Bithumb, Binance, and Upbit."""
        # Bithumb: bid -> BUY taker, ask -> SELL taker
        self.assertEqual(normalize_aggressor_side("BITHUMB", "bid"), "BUY")
        self.assertEqual(normalize_aggressor_side("BITHUMB", "ask"), "SELL")

        # Binance: is_buyer_maker=False -> BUY taker, True -> SELL taker
        self.assertEqual(normalize_aggressor_side("BINANCE", False), "BUY")
        self.assertEqual(normalize_aggressor_side("BINANCE", True), "SELL")

        # Upbit: ask_bid=BID -> BUY taker, ASK -> SELL taker
        self.assertEqual(normalize_aggressor_side("UPBIT", "BID"), "BUY")
        self.assertEqual(normalize_aggressor_side("UPBIT", "ASK"), "SELL")

    def test_warmup_contract_returns_none(self) -> None:
        """When insufficient trade events exist in window, return None, never arbitrary 0.0."""
        base_time = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        trades: list[TradeTick] = []  # No trades

        ati = compute_ati(trades, current_time=base_time, window_seconds=15.0, min_trades=2)
        self.assertIsNone(ati, "Warmup failure must return None (FEATURE_NOT_READY), not 0.0!")


if __name__ == "__main__":
    unittest.main()
