"""Deterministic Synthetic Microstructure and Chaos Injection Generator (P16 - P16.4).

FORENSIC HARDENING (Phase 2.5):
- BUG-5 FIXED: SignalMarketGenerator now has a real lag_steps parameter.
  Prior to this fix, imbalance and price change happened in the SAME loop
  iteration (contemporaneous), despite the docstring claiming "price moves
  after lag_steps". This is now corrected using a circular buffer.

  IMPORTANT: The prior behavior (contemporaneous signal) is documented as
  SignalMarketGenerator(lag_steps=0). lag_steps=0 is NOT lagged — it is
  the original contemporaneous behavior, retained for comparison tests.
  lag_steps=1 means the signal at time t affects price at t+1.

Provides:
- NullMarketGenerator: Gaussian random walk with realistic spread and depth (true zero alpha).
- SignalMarketGenerator: Known-signal injected market with configurable lag.
- ChaosInjector: Simulates network jitter, dropouts, burst arrivals, and spread blowouts.
"""

from __future__ import annotations

import collections
import math
import random
from typing import Iterator, Sequence

from .canonical_market_data import (
    CanonicalOrderBook,
    CanonicalTrade,
)


class NullMarketGenerator:
    """Generates a realistic orderbook stream driven by pure driftless geometric Brownian motion."""

    def __init__(
        self,
        initial_price: float = 100_000_000.0,
        volatility_bps_per_step: float = 1.0,
        spread_bps: float = 5.0,
        seed: int = 42,
    ) -> None:
        self.current_price = initial_price
        self.vol_step = volatility_bps_per_step / 10_000.0
        self.spread_frac = spread_bps / 10_000.0
        self.rng = random.Random(seed)
        self.step_idx = 0

    def generate_orderbooks(
        self, count: int, interval_ms: int = 100
    ) -> list[CanonicalOrderBook]:
        books = []
        base_time_ms = 1725500000000
        for i in range(count):
            # Geometric step
            shock = self.rng.gauss(0.0, self.vol_step)
            self.current_price *= math.exp(shock)
            half_spread = (self.current_price * self.spread_frac) / 2.0

            best_bid = round(self.current_price - half_spread, -3)  # Round to 1000 KRW
            best_ask = round(self.current_price + half_spread, -3)
            if best_bid >= best_ask:
                best_ask = best_bid + 1000.0

            bids = ((best_bid, 1.0), (best_bid - 1000.0, 2.0), (best_bid - 2000.0, 3.0))
            asks = ((best_ask, 1.0), (best_ask + 1000.0, 2.0), (best_ask + 2000.0, 3.0))

            t_ms = base_time_ms + i * interval_ms
            ob = CanonicalOrderBook(
                exchange="bithumb",
                market="KRW-BTC",
                exchange_timestamp_ms=t_ms,
                receive_timestamp_ms=t_ms + 10,
                bids=bids,
                asks=asks,
            )
            books.append(ob)
        return books


class SignalMarketGenerator:
    """Generates a market with an injected causal predictive relationship.

    When OFI is positive (bid size increases), price moves UP after lag_steps with correlation r.

    BUG-5 FIX: Prior implementation applied imbalance to price in the SAME loop iteration
    (contemporaneous), despite the docstring claiming "price moves after lag_steps".
    This is now corrected.

    lag_steps=0: CONTEMPORANEOUS — signal at time t affects price at t.
                 This matches the original (buggy) behavior for comparison.
    lag_steps=1: signal at time t affects price at t+1 (1-step ahead).
    lag_steps=N: signal at time t affects price at t+N.

    NOTE: With lag_steps=0, any predictive model trained on this data would
    only work if it has access to the same-timestep signal — which is
    typically not available in real trading (you receive the signal THEN trade).
    Use lag_steps >= 1 for realistic predictive relationship testing.
    """

    def __init__(
        self,
        initial_price: float = 100_000_000.0,
        signal_strength: float = 0.0005,  # 5 bps predictable drift per unit OFI
        lag_steps: int = 1,  # BUG-5 FIX: default is 1-step lag (not contemporaneous)
        seed: int = 123,
    ) -> None:
        if lag_steps < 0:
            raise ValueError(f"lag_steps must be >= 0, got {lag_steps}")
        self.current_price = initial_price
        self.signal_strength = signal_strength
        self.lag_steps = lag_steps
        self.rng = random.Random(seed)
        # BUG-5 FIX: circular buffer to hold pending signals
        # deque maxlen=lag_steps+1; deque[0] is the oldest signal (to be applied now)
        self._signal_buffer: collections.deque[float] = collections.deque(
            [0.0] * lag_steps, maxlen=lag_steps if lag_steps > 0 else 1
        )

    def generate_signal_orderbooks(
        self, count: int, interval_ms: int = 100
    ) -> tuple[list[CanonicalOrderBook], list[float]]:
        books = []
        injected_signals = []
        base_time_ms = 1725500000000

        for i in range(count):
            # Random intentional imbalance (-2 to +2 BTC)
            imbalance = self.rng.choice([-2.0, -1.0, 0.0, 1.0, 2.0])
            injected_signals.append(imbalance)

            if self.lag_steps == 0:
                # CONTEMPORANEOUS: original (buggy) behavior — kept for comparison
                lagged_signal = imbalance
            else:
                # BUG-5 FIX: use buffered signal from lag_steps ago
                lagged_signal = self._signal_buffer[0]  # oldest buffered signal
                self._signal_buffer.append(imbalance)    # enqueue current signal

            drift = lagged_signal * self.signal_strength
            noise = self.rng.gauss(0.0, 0.0001)
            self.current_price *= (1.0 + drift + noise)

            best_bid = round(self.current_price - 5000.0, -3)
            best_ask = round(self.current_price + 5000.0, -3)
            if best_bid >= best_ask:
                best_ask = best_bid + 1000.0

            bid_depth = max(0.5, 2.0 + imbalance)
            ask_depth = max(0.5, 2.0 - imbalance)

            bids = ((best_bid, bid_depth),)
            asks = ((best_ask, ask_depth),)

            t_ms = base_time_ms + i * interval_ms
            ob = CanonicalOrderBook(
                exchange="bithumb",
                market="KRW-BTC",
                exchange_timestamp_ms=t_ms,
                receive_timestamp_ms=t_ms + 10,
                bids=bids,
                asks=asks,
            )
            books.append(ob)
        return books, injected_signals


class ChaosInjector:
    """Injects synthetic network disruptions, packet reordering, and liquidity shocks."""

    def __init__(self, seed: int = 999) -> None:
        self.rng = random.Random(seed)

    def inject_disruptions(
        self,
        orderbooks: Sequence[CanonicalOrderBook],
        drop_rate: float = 0.05,
        max_jitter_ms: int = 200,
        spread_blowout_rate: float = 0.02,
    ) -> list[CanonicalOrderBook]:
        result = []
        for ob in orderbooks:
            # 1. Packet drop
            if self.rng.random() < drop_rate:
                continue

            # 2. Network jitter on receive timestamp
            jitter = self.rng.randint(-10, max_jitter_ms)
            new_receive = max(ob.exchange_timestamp_ms, ob.receive_timestamp_ms + jitter)

            # 3. Spread blowout
            bids = ob.bids
            asks = ob.asks
            if self.rng.random() < spread_blowout_rate:
                # Blow out spread by 10x
                blowout_bid = (bids[0][0] - 50_000.0, bids[0][1])
                blowout_ask = (asks[0][0] + 50_000.0, asks[0][1])
                bids = (blowout_bid,) + bids[1:]
                asks = (blowout_ask,) + asks[1:]

            mutated = CanonicalOrderBook(
                exchange=ob.exchange,
                market=ob.market,
                exchange_timestamp_ms=ob.exchange_timestamp_ms,
                receive_timestamp_ms=new_receive,
                bids=bids,
                asks=asks,
                schema_version=ob.schema_version,
                timestamp_semantics=ob.timestamp_semantics,
                is_snapshot=ob.is_snapshot,
            )
            result.append(mutated)
        return result
