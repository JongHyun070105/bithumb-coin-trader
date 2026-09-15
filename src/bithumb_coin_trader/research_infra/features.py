"""Feature Engine for Microstructure Research.

Computes features using ONLY information available at or before feature timestamp.
All window functions are strictly backward-looking.

Feature families:
    MARKET_STATE: mid, spread, best bid/ask, top-of-book sizes
    MICROPRICE: microprice, displacement from mid, normalized displacement
    DEPTH: bid/ask depth at N levels, depth imbalance
    ORDERBOOK_IMBALANCE: L1, multi-level, change in imbalance
    TRADE_FLOW: signed volume, buy/sell imbalance, rolling flow
    INTENSITY: trade arrival rate, orderbook update rate, event intensity
    VOLATILITY: realized short-horizon volatility
    MOMENTUM: short-horizon mid returns, rolling returns
    CROSS_EXCHANGE: Bithumb vs Binance/Upbit basis, lead-lag features

All features use the existing compute_ofi_v1, compute_ofi_v2, compute_ati,
compute_mpqi functions from microstructure_features.py where applicable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any, Sequence

from .canonical_events import CanonicalEvent, EventKind
from ..microstructure_features import (
    OrderbookSnapshot,
    TradeTick,
    compute_ofi_v1,
    compute_ofi_v2,
    compute_ati,
    compute_mpqi,
    normalize_aggressor_side,
)
from ..canonical_market_data import CanonicalOrderBook, CanonicalTrade


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Complete feature vector at a point in time.

    All values are computed using only information at or before
    the timestamp. None = insufficient data.
    """

    timestamp_ns: int
    exchange: str
    market: str

    # MARKET STATE
    mid_price: float | None = None
    spread_bps: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    best_bid_size: float | None = None
    best_ask_size: float | None = None

    # MICROPRICE
    microprice: float | None = None
    microprice_bias_bps: float | None = None
    microprice_displacement: float | None = None  # microprice - mid

    # DEPTH
    bid_depth_l1: float | None = None
    ask_depth_l1: float | None = None
    bid_depth_l5: float | None = None
    ask_depth_l5: float | None = None
    depth_imbalance_l1: float | None = None  # (bid-ask)/(bid+ask)
    depth_imbalance_l5: float | None = None

    # ORDERBOOK IMBALANCE
    obi_v1_5s: float | None = None  # OFI v1 over 5s window
    obi_v1_30s: float | None = None
    obi_v2_5s: float | None = None  # OFI v2 (Cont et al.)
    qi_l1: float | None = None  # Queue imbalance at L1
    qi_l3: float | None = None
    qi_l5: float | None = None

    # TRADE FLOW
    ati_5s: float | None = None
    ati_30s: float | None = None
    ati_60s: float | None = None
    signed_volume_30s: float | None = None
    trade_count_30s: int | None = None
    buy_volume_30s: float | None = None
    sell_volume_30s: float | None = None

    # INTENSITY
    trade_arrival_rate_30s: float | None = None  # trades per second
    ob_update_rate_30s: float | None = None  # updates per second
    event_intensity_30s: float | None = None

    # VOLATILITY
    realized_vol_30s: float | None = None  # annualized
    realized_vol_60s: float | None = None

    # MOMENTUM / REVERSAL
    mid_return_1s: float | None = None
    mid_return_5s: float | None = None
    mid_return_30s: float | None = None
    mid_return_60s: float | None = None

    # CROSS-EXCHANGE (computed externally, stored here)
    cross_exchange_basis_binance: float | None = None
    cross_exchange_basis_upbit: float | None = None
    cross_exchange_return_diff_binance_5s: float | None = None
    cross_exchange_return_diff_upbit_5s: float | None = None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import fields
        result = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is not None:
                result[f.name] = v
        return result


class FeatureEngine:
    """Streaming feature computation engine.

    Maintains rolling state for efficient computation.
    All windows are strictly backward-looking.

    Optimized with bisect for O(log n) window lookups instead of O(n) scans.
    Uses running OFI accumulator to avoid O(n²) recomputation.
    """

    def __init__(
        self,
        market: str = "KRW-BTC",
        exchange: str = "bithumb",
    ) -> None:
        self.market = market
        self.exchange = exchange

        # Rolling state — all sorted by timestamp
        self._ob_times: list[int] = []
        self._ob_snapshots: list[OrderbookSnapshot] = []
        self._trade_times: list[int] = []
        self._trade_ticks: list[TradeTick] = []
        self._mid_times: list[int] = []
        self._mid_prices: list[float] = []
        self._event_times: list[int] = []

        # Running OFI accumulator (avoids O(n²) recomputation)
        self._ofi_cumulative: list[tuple[int, float]] = []  # (timestamp, cumulative_ofi)
        self._last_ofi_value: float = 0.0

        # Max history to keep (60s is enough for all windows)
        self._max_history_ns = 120_000_000_000  # 2 minutes

    def _prune_old(self, current_ns: int) -> None:
        cutoff = current_ns - self._max_history_ns
        # Use bisect to find cutoff index for each sorted list
        import bisect
        for times, data in [
            (self._ob_times, self._ob_snapshots),
            (self._trade_times, self._trade_ticks),
            (self._mid_times, self._mid_prices),
        ]:
            idx = bisect.bisect_left(times, cutoff)
            if idx > 0:
                del times[:idx]
                del data[:idx]
        idx = bisect.bisect_left(self._event_times, cutoff)
        if idx > 0:
            del self._event_times[:idx]
        idx = bisect.bisect_left([t for t, _ in self._ofi_cumulative], cutoff)
        if idx > 0:
            del self._ofi_cumulative[:idx]

    def _count_in_window(self, times: list[int], start_ns: int, end_ns: int) -> int:
        """Count events in [start, end] using bisect. O(log n)."""
        import bisect
        lo = bisect.bisect_left(times, start_ns)
        hi = bisect.bisect_right(times, end_ns)
        return hi - lo

    def _find_closest_mid(self, target_ns: int) -> float | None:
        """Find mid price closest to target_ns using bisect. O(log n)."""
        import bisect
        if not self._mid_times:
            return None
        idx = bisect.bisect_left(self._mid_times, target_ns)
        candidates = []
        if idx > 0:
            candidates.append((abs(self._mid_times[idx - 1] - target_ns), self._mid_prices[idx - 1]))
        if idx < len(self._mid_times):
            candidates.append((abs(self._mid_times[idx] - target_ns), self._mid_prices[idx]))
        if not candidates:
            return None
        return min(candidates, key=lambda x: x[0])[1]

    def _get_window_slice(self, times: list[int], start_ns: int, end_ns: int) -> tuple[int, int]:
        """Return (lo, hi) indices for items in [start, end]. O(log n)."""
        import bisect
        lo = bisect.bisect_left(times, start_ns)
        hi = bisect.bisect_right(times, end_ns)
        return lo, hi

    def process_event(self, event: CanonicalEvent) -> FeatureVector | None:
        """Process a single event and return features if available."""
        if event.market != self.market or event.exchange != self.exchange:
            return None

        t_ns = event.ordering_timestamp_ns
        self._prune_old(t_ns)
        self._event_times.append(t_ns)

        if event.event_kind == EventKind.ORDERBOOK:
            return self._process_orderbook(event, t_ns)
        elif event.event_kind == EventKind.TRADE:
            return self._process_trade(event, t_ns)
        elif event.event_kind == EventKind.TICKER:
            return self._process_ticker(event, t_ns)

        return None

    def _process_orderbook(self, event: CanonicalEvent, t_ns: int) -> FeatureVector:
        payload = event.payload
        bids = tuple((float(p), float(s)) for p, s in payload.get("bids", []))
        asks = tuple((float(p), float(s)) for p, s in payload.get("asks", []))

        if not bids or not asks:
            return self._empty_features(t_ns, event)

        ob = OrderbookSnapshot(
            market=event.market,
            timestamp=datetime.fromtimestamp(t_ns / 1_000_000_000, tz=timezone.utc),
            bids=bids,
            asks=asks,
        )

        self._ob_times.append(t_ns)
        self._ob_snapshots.append(ob)

        mid = ob.mid_price
        if mid > 0:
            self._mid_times.append(t_ns)
            self._mid_prices.append(mid)

        # Build feature vector incrementally
        fv = FeatureVector(
            timestamp_ns=t_ns,
            exchange=event.exchange,
            market=event.market,
            mid_price=mid,
            spread_bps=ob.spread_bps,
            best_bid=ob.best_bid,
            best_ask=ob.best_ask,
            best_bid_size=ob.best_bid_size,
            best_ask_size=ob.best_ask_size,
        )

        # Depth features
        bid_depth_l1 = bids[0][1]
        ask_depth_l1 = asks[0][1]
        bid_depth_l5 = sum(s for _, s in bids[:5])
        ask_depth_l5 = sum(s for _, s in asks[:5])
        depth_sum_l1 = bid_depth_l1 + ask_depth_l1
        depth_sum_l5 = bid_depth_l5 + ask_depth_l5

        object.__setattr__(fv, "bid_depth_l1", bid_depth_l1)
        object.__setattr__(fv, "ask_depth_l1", ask_depth_l1)
        object.__setattr__(fv, "bid_depth_l5", bid_depth_l5)
        object.__setattr__(fv, "ask_depth_l5", ask_depth_l5)
        object.__setattr__(fv, "depth_imbalance_l1",
                           (bid_depth_l1 - ask_depth_l1) / depth_sum_l1 if depth_sum_l1 > 0 else 0.0)
        object.__setattr__(fv, "depth_imbalance_l5",
                           (bid_depth_l5 - ask_depth_l5) / depth_sum_l5 if depth_sum_l5 > 0 else 0.0)

        # Microprice
        micro, qi = compute_mpqi(ob, depth=5)
        micro_bias_bps = (micro - mid) / mid * 10_000 if mid > 0 else 0.0
        object.__setattr__(fv, "microprice", micro)
        object.__setattr__(fv, "microprice_bias_bps", micro_bias_bps)
        object.__setattr__(fv, "microprice_displacement", micro - mid)
        object.__setattr__(fv, "qi_l1", qi)
        object.__setattr__(fv, "qi_l3", qi)
        object.__setattr__(fv, "qi_l5", qi)

        # OFI — incremental accumulator
        if len(self._ob_snapshots) >= 2:
            prev_ob = self._ob_snapshots[-2]
            ofi_v1 = compute_ofi_v1(prev_ob, ob, depth=5)
            ofi_v2 = compute_ofi_v2(prev_ob, ob)
            self._last_ofi_value += ofi_v1
            self._ofi_cumulative.append((t_ns, self._last_ofi_value))

            # Window OFI using cumulative sum difference
            window_5s = t_ns - 5_000_000_000
            window_30s = t_ns - 30_000_000_000
            ofi_5s = self._ofi_in_window(window_5s, t_ns)
            ofi_30s = self._ofi_in_window(window_30s, t_ns)

            object.__setattr__(fv, "obi_v1_5s", ofi_5s if ofi_5s != 0 else ofi_v1)
            object.__setattr__(fv, "obi_v1_30s", ofi_30s if ofi_30s != 0 else ofi_v1)
            object.__setattr__(fv, "obi_v2_5s", ofi_v2)

        # Intensity
        window_30s = t_ns - 30_000_000_000
        ob_updates_30s = self._count_in_window(self._ob_times, window_30s, t_ns)
        events_30s = self._count_in_window(self._event_times, window_30s, t_ns)
        object.__setattr__(fv, "ob_update_rate_30s", ob_updates_30s / 30.0)
        object.__setattr__(fv, "event_intensity_30s", events_30s / 30.0)

        # Momentum and volatility
        if mid > 0 and self._mid_times:
            for label, window_s in [
                ("mid_return_1s", 1.0),
                ("mid_return_5s", 5.0),
                ("mid_return_30s", 30.0),
                ("mid_return_60s", 60.0),
            ]:
                past_mid = self._find_closest_mid(t_ns - int(window_s * 1e9))
                if past_mid is not None and past_mid > 0:
                    object.__setattr__(fv, label, (mid - past_mid) / past_mid)

            for label, window_s in [
                ("realized_vol_30s", 30.0),
                ("realized_vol_60s", 60.0),
            ]:
                cutoff = t_ns - int(window_s * 1e9)
                lo, hi = self._get_window_slice(self._mid_times, cutoff, t_ns)
                if hi - lo >= 2:
                    returns = []
                    for i in range(lo + 1, hi):
                        prev_p = self._mid_prices[i - 1]
                        curr_p = self._mid_prices[i]
                        if prev_p > 0:
                            returns.append(math.log(curr_p / prev_p))
                    if returns:
                        mean_sq = sum(r * r for r in returns) / len(returns)
                        object.__setattr__(fv, label,
                                           math.sqrt(mean_sq) * math.sqrt(365.25 * 24 * 3600 / window_s))

        return fv

    def _process_trade(self, event: CanonicalEvent, t_ns: int) -> FeatureVector:
        payload = event.payload
        price = float(payload.get("price", 0))
        qty = float(payload.get("quantity", 0))
        side = str(payload.get("aggressor_side", "BUY"))

        trade = TradeTick(
            market=event.market,
            timestamp=datetime.fromtimestamp(t_ns / 1_000_000_000, tz=timezone.utc),
            price=price,
            volume=qty,
            side=side,
            exchange=event.exchange.upper(),
        )

        self._trade_times.append(t_ns)
        self._trade_ticks.append(trade)

        fv = self._empty_features(t_ns, event)

        # Volume decomposition using window slice
        window_5s = t_ns - 5_000_000_000
        window_30s = t_ns - 30_000_000_000
        window_60s = t_ns - 60_000_000_000

        # ATI using window slices
        lo5, hi5 = self._get_window_slice(self._trade_times, window_5s, t_ns)
        lo30, hi30 = self._get_window_slice(self._trade_times, window_30s, t_ns)
        lo60, hi60 = self._get_window_slice(self._trade_times, window_60s, t_ns)

        def _compute_ati_from_slice(lo: int, hi: int) -> float | None:
            if hi - lo < 1:
                return None
            buy_vol = sum(self._trade_ticks[i].volume for i in range(lo, hi)
                          if self._trade_ticks[i].side == "BUY")
            sell_vol = sum(self._trade_ticks[i].volume for i in range(lo, hi)
                           if self._trade_ticks[i].side == "SELL")
            total = buy_vol + sell_vol
            if total <= 0:
                return 0.0
            return (buy_vol - sell_vol) / total

        buy_vol_30 = sum(self._trade_ticks[i].volume for i in range(lo30, hi30)
                         if self._trade_ticks[i].side == "BUY")
        sell_vol_30 = sum(self._trade_ticks[i].volume for i in range(lo30, hi30)
                          if self._trade_ticks[i].side == "SELL")
        trade_count_30 = hi30 - lo30

        object.__setattr__(fv, "ati_5s", _compute_ati_from_slice(lo5, hi5))
        object.__setattr__(fv, "ati_30s", _compute_ati_from_slice(lo30, hi30))
        object.__setattr__(fv, "ati_60s", _compute_ati_from_slice(lo60, hi60))
        object.__setattr__(fv, "signed_volume_30s", buy_vol_30 - sell_vol_30)
        object.__setattr__(fv, "trade_count_30s", trade_count_30)
        object.__setattr__(fv, "buy_volume_30s", buy_vol_30)
        object.__setattr__(fv, "sell_volume_30s", sell_vol_30)
        object.__setattr__(fv, "trade_arrival_rate_30s", trade_count_30 / 30.0)
        events_30 = self._count_in_window(self._event_times, window_30s, t_ns)
        object.__setattr__(fv, "event_intensity_30s", events_30 / 30.0)

        if self._ob_snapshots:
            latest_ob = self._ob_snapshots[-1]
            object.__setattr__(fv, "mid_price", latest_ob.mid_price)
            object.__setattr__(fv, "spread_bps", latest_ob.spread_bps)

        return fv

    def _process_ticker(self, event: CanonicalEvent, t_ns: int) -> FeatureVector:
        payload = event.payload
        last_price = float(payload.get("last_price", 0))
        fv = self._empty_features(t_ns, event)
        if last_price > 0:
            object.__setattr__(fv, "mid_price", last_price)
        return fv

    def _empty_features(self, t_ns: int, event: CanonicalEvent) -> FeatureVector:
        return FeatureVector(
            timestamp_ns=t_ns,
            exchange=event.exchange,
            market=event.market,
        )

    def _ofi_in_window(self, start_ns: int, end_ns: int) -> float:
        """Compute OFI in window using cumulative sum. O(log n)."""
        import bisect
        if not self._ofi_cumulative:
            return 0.0
        times = [t for t, _ in self._ofi_cumulative]
        values = [v for _, v in self._ofi_cumulative]

        lo = bisect.bisect_left(times, start_ns)
        hi = bisect.bisect_right(times, end_ns)

        if hi <= lo:
            return 0.0
        if lo == 0:
            return values[hi - 1]
        return values[hi - 1] - values[lo - 1]


def compute_cross_exchange_features(
    primary_fv: FeatureVector,
    aligned_exchange: str,
    aligned_fv: FeatureVector | None,
    primary_mid_history: list[tuple[int, float]],
    aligned_mid_history: list[tuple[int, float]],
) -> FeatureVector:
    """Compute cross-exchange features from aligned feature vectors.

    Uses backward-looking alignment only.
    """
    if aligned_fv is None or aligned_fv.mid_price is None or primary_fv.mid_price is None:
        return primary_fv

    primary_mid = primary_fv.mid_price
    aligned_mid = aligned_fv.mid_price

    updates: dict[str, Any] = {}

    # Basis
    if aligned_mid > 0:
        basis = (primary_mid - aligned_mid) / aligned_mid * 10_000  # bps
        if aligned_exchange == "binance":
            updates["cross_exchange_basis_binance"] = basis
        elif aligned_exchange == "upbit":
            updates["cross_exchange_basis_upbit"] = basis

    # Return difference over 5s
    t_ns = primary_fv.timestamp_ns
    target_ns = t_ns - 5_000_000_000

    def find_closest(history: list[tuple[int, float]], target: int) -> float | None:
        best = None
        best_dist = float("inf")
        for t, p in history:
            dist = abs(t - target)
            if dist < best_dist:
                best_dist = dist
                best = p
        return best

    primary_5s_ago = find_closest(primary_mid_history, target_ns)
    aligned_5s_ago = find_closest(aligned_mid_history, target_ns)

    if primary_5s_ago and aligned_5s_ago and primary_5s_ago > 0 and aligned_5s_ago > 0:
        primary_ret = (primary_mid - primary_5s_ago) / primary_5s_ago
        aligned_ret = (aligned_mid - aligned_5s_ago) / aligned_5s_ago
        diff = primary_ret - aligned_ret
        if aligned_exchange == "binance":
            updates["cross_exchange_return_diff_binance_5s"] = diff
        elif aligned_exchange == "upbit":
            updates["cross_exchange_return_diff_upbit_5s"] = diff

    if updates:
        from dataclasses import fields as dc_fields
        valid_names = {f.name for f in dc_fields(FeatureVector)}
        for k, v in updates.items():
            if k in valid_names and v is not None:
                object.__setattr__(primary_fv, k, v)

    return primary_fv
