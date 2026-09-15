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
    """

    def __init__(
        self,
        market: str = "KRW-BTC",
        exchange: str = "bithumb",
    ) -> None:
        self.market = market
        self.exchange = exchange

        # Rolling state
        self._orderbook_history: list[tuple[int, OrderbookSnapshot]] = []
        self._trade_history: list[tuple[int, TradeTick]] = []
        self._mid_history: list[tuple[int, float]] = []
        self._event_times: list[int] = []
        self._ob_update_times: list[int] = []

        # Max history to keep (prevent unbounded growth)
        self._max_history_ns = 300_000_000_000  # 5 minutes

    def _prune_old(self, current_ns: int) -> None:
        cutoff = current_ns - self._max_history_ns
        self._orderbook_history = [
            (t, s) for t, s in self._orderbook_history if t >= cutoff
        ]
        self._trade_history = [
            (t, s) for t, s in self._trade_history if t >= cutoff
        ]
        self._mid_history = [
            (t, p) for t, p in self._mid_history if t >= cutoff
        ]
        self._event_times = [t for t in self._event_times if t >= cutoff]
        self._ob_update_times = [t for t in self._ob_update_times if t >= cutoff]

    def _seconds_ago(self, current_ns: int, window_s: float) -> int:
        return current_ns - int(window_s * 1_000_000_000)

    def _count_in_window(self, times: list[int], start_ns: int, end_ns: int) -> int:
        return sum(1 for t in times if start_ns <= t <= end_ns)

    def process_event(self, event: CanonicalEvent) -> FeatureVector | None:
        """Process a single event and return features if available.

        Only processes events for the configured market and exchange.
        Returns None if event is for a different market/exchange.
        """
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

        self._orderbook_history.append((t_ns, ob))
        self._ob_update_times.append(t_ns)

        # Update mid history
        mid = ob.mid_price
        if mid > 0:
            self._mid_history.append((t_ns, mid))

        # Compute features
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
        bid_depth_l1 = sum(s for _, s in bids[:1])
        ask_depth_l1 = sum(s for _, s in asks[:1])
        bid_depth_l5 = sum(s for _, s in bids[:5])
        ask_depth_l5 = sum(s for _, s in asks[:5])

        depth_imb_l1 = (bid_depth_l1 - ask_depth_l1) / (bid_depth_l1 + ask_depth_l1) \
            if (bid_depth_l1 + ask_depth_l1) > 0 else 0.0
        depth_imb_l5 = (bid_depth_l5 - ask_depth_l5) / (bid_depth_l5 + ask_depth_l5) \
            if (bid_depth_l5 + ask_depth_l5) > 0 else 0.0

        fv = FeatureVector(
            **{**fv.to_dict(), "bid_depth_l1": bid_depth_l1, "ask_depth_l1": ask_depth_l1,
               "bid_depth_l5": bid_depth_l5, "ask_depth_l5": ask_depth_l5,
               "depth_imbalance_l1": depth_imb_l1, "depth_imbalance_l5": depth_imb_l5},
        )

        # Microprice
        micro, qi = compute_mpqi(ob, depth=5)
        if mid > 0:
            micro_bias_bps = (micro - mid) / mid * 10_000
        else:
            micro_bias_bps = 0.0

        fv = FeatureVector(
            **{**fv.to_dict(), "microprice": micro, "microprice_bias_bps": micro_bias_bps,
               "microprice_displacement": micro - mid,
               "qi_l1": qi, "qi_l3": qi, "qi_l5": qi},
        )

        # Orderbook imbalance (OFI)
        if len(self._orderbook_history) >= 2:
            prev_ob = self._orderbook_history[-2][1]
            prev_t = self._orderbook_history[-2][0]

            ofi_v1 = compute_ofi_v1(prev_ob, ob, depth=5)
            ofi_v2 = compute_ofi_v2(prev_ob, ob)

            # Accumulate OFI over windows
            window_5s = self._seconds_ago(t_ns, 5.0)
            window_30s = self._seconds_ago(t_ns, 30.0)

            ofi_v1_5s = self._accumulate_ofi(window_5s, t_ns)
            ofi_v1_30s = self._accumulate_ofi(window_30s, t_ns)

            fv = FeatureVector(
                **{**fv.to_dict(),
                   "obi_v1_5s": ofi_v1_5s if ofi_v1_5s != 0 else ofi_v1,
                   "obi_v1_30s": ofi_v1_30s if ofi_v1_30s != 0 else ofi_v1,
                   "obi_v2_5s": ofi_v2},
            )

        # Intensity
        window_30s = self._seconds_ago(t_ns, 30.0)
        ob_updates_30s = self._count_in_window(self._ob_update_times, window_30s, t_ns)
        events_30s = self._count_in_window(self._event_times, window_30s, t_ns)

        fv = FeatureVector(
            **{**fv.to_dict(),
               "ob_update_rate_30s": ob_updates_30s / 30.0,
               "event_intensity_30s": events_30s / 30.0},
        )

        # Volatility and momentum from mid history
        self._enrich_momentum_vol(fv, t_ns)

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

        self._trade_history.append((t_ns, trade))
        self._event_times.append(t_ns)

        fv = self._empty_features(t_ns, event)

        # Trade flow features
        window_5s = self._seconds_ago(t_ns, 5.0)
        window_30s = self._seconds_ago(t_ns, 30.0)
        window_60s = self._seconds_ago(t_ns, 60.0)

        # ATI
        trade_ticks = [t for _, t in self._trade_history]
        ati_5s = compute_ati(trade_ticks, trade.timestamp, 5.0)
        ati_30s = compute_ati(trade_ticks, trade.timestamp, 30.0)
        ati_60s = compute_ati(trade_ticks, trade.timestamp, 60.0)

        # Volume decomposition
        trades_30s = [
            (t, tr) for t, tr in self._trade_history
            if window_30s <= t <= t_ns
        ]
        buy_vol = sum(tr.volume for _, tr in trades_30s if tr.side == "BUY")
        sell_vol = sum(tr.volume for _, tr in trades_30s if tr.side == "SELL")
        signed_vol = buy_vol - sell_vol
        trade_count = len(trades_30s)

        fv = FeatureVector(
            **{**fv.to_dict(),
               "ati_5s": ati_5s,
               "ati_30s": ati_30s,
               "ati_60s": ati_60s,
               "signed_volume_30s": signed_vol,
               "trade_count_30s": trade_count,
               "buy_volume_30s": buy_vol,
               "sell_volume_30s": sell_vol,
               "trade_arrival_rate_30s": trade_count / 30.0,
               "event_intensity_30s": len([t for t in self._event_times
                                           if window_30s <= t <= t_ns]) / 30.0},
        )

        # Mid price from latest orderbook
        if self._orderbook_history:
            latest_ob = self._orderbook_history[-1][1]
            fv = FeatureVector(
                **{**fv.to_dict(), "mid_price": latest_ob.mid_price,
                   "spread_bps": latest_ob.spread_bps},
            )

        return fv

    def _process_ticker(self, event: CanonicalEvent, t_ns: int) -> FeatureVector:
        payload = event.payload
        last_price = float(payload.get("last_price", 0))
        fv = self._empty_features(t_ns, event)
        if last_price > 0:
            fv = FeatureVector(**{**fv.to_dict(), "mid_price": last_price})
        return fv

    def _empty_features(self, t_ns: int, event: CanonicalEvent) -> FeatureVector:
        return FeatureVector(
            timestamp_ns=t_ns,
            exchange=event.exchange,
            market=event.market,
        )

    def _accumulate_ofi(self, start_ns: int, end_ns: int) -> float:
        """Accumulate OFI over a window from orderbook history."""
        total = 0.0
        obs_in_window = [(t, ob) for t, ob in self._orderbook_history
                         if start_ns <= t <= end_ns]
        for i in range(1, len(obs_in_window)):
            prev_ob = obs_in_window[i - 1][1]
            curr_ob = obs_in_window[i][1]
            total += compute_ofi_v1(prev_ob, curr_ob, depth=5)
        return total

    def _enrich_momentum_vol(self, fv: FeatureVector, t_ns: int) -> None:
        """Add momentum and volatility features from mid price history."""
        if not self._mid_history or fv.mid_price is None or fv.mid_price <= 0:
            return

        mid = fv.mid_price
        updates: dict[str, Any] = {}

        for label, window_s in [
            ("mid_return_1s", 1.0),
            ("mid_return_5s", 5.0),
            ("mid_return_30s", 30.0),
            ("mid_return_60s", 60.0),
        ]:
            target_ns = t_ns - int(window_s * 1_000_000_000)
            # Find closest mid to target
            best = None
            best_dist = float("inf")
            for t, p in self._mid_history:
                dist = abs(t - target_ns)
                if dist < best_dist:
                    best_dist = dist
                    best = p
            if best is not None and best > 0:
                updates[label] = (mid - best) / best

        # Realized volatility (annualized from returns)
        for label, window_s in [
            ("realized_vol_30s", 30.0),
            ("realized_vol_60s", 60.0),
        ]:
            cutoff = t_ns - int(window_s * 1_000_000_000)
            window_mids = [(t, p) for t, p in self._mid_history if t >= cutoff]
            if len(window_mids) >= 2:
                returns = []
                for i in range(1, len(window_mids)):
                    prev_p = window_mids[i - 1][1]
                    curr_p = window_mids[i][1]
                    if prev_p > 0:
                        returns.append(math.log(curr_p / prev_p))
                if returns:
                    mean_sq = sum(r * r for r in returns) / len(returns)
                    # Annualize: sqrt(mean_sq * events_per_year)
                    # Rough: use 365.25 * 24 * 3600 seconds per year
                    updates[label] = math.sqrt(mean_sq) * math.sqrt(365.25 * 24 * 3600 / window_s)

        if updates:
            from dataclasses import fields as dc_fields
            valid_names = {f.name for f in dc_fields(FeatureVector)}
            for k, v in updates.items():
                if k in valid_names and v is not None:
                    object.__setattr__(fv, k, v)


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
